"""Dash dashboard: late deliveries in Brazilian e-commerce (Olist).

Run:  python dashboard/app.py   then open http://127.0.0.1:8050
It reads ONLY the warehouse views (vw_order_late, vw_chart_*, vw_data_health) and never changes the data.

How the numbers work: every view keeps order-level columns (state, year, holiday flags) next to
order counts, so we add counts up after filtering and divide once:
    late rate = sum(late orders) / sum(orders)      (an order with 3 items counts once)
"""
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "warehouse" / "warehouse.db"
NOTE = "Rate per order, orders counted once (an order with several items counts once in each band, route or category it contains)."
LATE_COLOR, ONTIME_COLOR = "#d95f02", "#1b9e77"


# ---------------------------------------------------------------- reading the views

def db_path():
    return Path(os.environ.get("WAREHOUSE_PATH", DEFAULT_DB))


def read(sql, params=()):
    """Run a SELECT on the warehouse (opened read-only) and return a DataFrame."""
    conn = sqlite3.connect(f"{db_path().resolve().as_uri()}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def where_clause(state, year, exclude_carnival):
    """WHERE text and parameters for the three order-level filters. None = no filter."""
    parts, params = [], []
    if state:
        parts.append("customer_state = ?")
        params.append(state)
    if year:
        parts.append("purchase_year = ?")
        params.append(int(year))
    if exclude_carnival:
        parts.append("carnival_in_window = 0")
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def filter_choices():
    """Options for the two dropdowns, read from the views."""
    states = read("SELECT DISTINCT customer_state FROM vw_order_late ORDER BY 1")["customer_state"].tolist()
    years = read("SELECT DISTINCT purchase_year FROM vw_order_late ORDER BY 1")["purchase_year"].tolist()
    return states, years


# ---------------------------------------------------------------- header numbers

def header_numbers(state, year, exclude_carnival):
    """Overall late rate, delivered orders, orders set aside, and the holiday effect."""
    where, params = where_clause(state, year, exclude_carnival)
    totals = read("SELECT COUNT(*) AS orders, COALESCE(SUM(is_late), 0) AS late, "
                  "COALESCE(SUM(CASE WHEN is_late = 1 THEN revenue_usd END), 0) AS late_revenue "
                  "FROM vw_order_late" + where, params).iloc[0]
    by_holiday = read("SELECT holiday_in_window AS h, COUNT(*) AS orders, SUM(is_late) AS late "
                      "FROM vw_order_late" + where + " GROUP BY holiday_in_window", params)
    health = read("SELECT metric, value FROM vw_data_health").set_index("metric")["value"]
    rates = {int(r.h): (r.late / r.orders if r.orders else None) for r in by_holiday.itertuples()}
    return {"orders": int(totals["orders"]), "late": int(totals["late"]),
            "rate": (totals["late"] / totals["orders"]) if totals["orders"] else None,
            "late_revenue": float(totals["late_revenue"]),
            "rate_holiday": rates.get(1), "rate_no_holiday": rates.get(0),
            "out_of_scope": int(health["orders_out_of_scope"]),
            "quarantined": int(health["orders_quarantined"])}


def pct(value):
    return "n/a" if value is None else f"{value:.1%}"


def kpi_cards(numbers):
    """The row of big numbers at the top."""
    def card(title, value, small=""):
        return html.Div([html.Div(title, className="kpi-title"), html.Div(value, className="kpi-value"),
                         html.Div(small, className="kpi-small")], className="kpi")
    return [
        card("Late rate (per order)", pct(numbers["rate"]), f"{numbers['late']:,} late of {numbers['orders']:,}"),
        card("Delivered orders in scope", f"{numbers['orders']:,}"),
        card("Revenue on late orders", f"US$ {numbers['late_revenue']:,.0f}", "item prices; not revenue lost"),
        card("Holiday in window", pct(numbers["rate_holiday"]),
             f"vs {pct(numbers['rate_no_holiday'])} with no national holiday"),
        card("Orders set aside", f"{numbers['out_of_scope'] + numbers['quarantined']:,}",
             f"{numbers['out_of_scope']:,} out of scope + {numbers['quarantined']:,} quarantined (all data, not filtered)"),
    ]


# ---------------------------------------------------------------- figures

def empty_figure(message="No data for this selection (try another state or year, or lower the minimum orders)."):
    """A blank chart with a message, so an empty selection never crashes."""
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font={"size": 14})
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(height=380, margin={"t": 30})
    return fig


def band_frame(state, year, exclude_carnival, min_orders):
    """Chart 1 data: orders and late orders per band, bands under the minimum dropped."""
    where, params = where_clause(state, year, exclude_carnival)
    sql = ("SELECT band_type, band, SUM(orders) AS orders, SUM(late_orders) AS late_orders "
           "FROM vw_chart_distance_freight" + where + " GROUP BY band_type, band HAVING SUM(orders) >= ? ORDER BY band")
    df = read(sql, params + [int(min_orders)])
    df["late_rate"] = df["late_orders"] / df["orders"]
    return df


def distance_freight_figure(state, year, exclude_carnival, min_orders):
    df = band_frame(state, year, exclude_carnival, min_orders)
    if df.empty:
        return empty_figure()
    fig = make_subplots(rows=1, cols=2, subplot_titles=("By seller-customer distance", "By freight as a share of price"))
    for col, kind in ((1, "distance"), (2, "freight")):
        part = df[df["band_type"] == kind]
        fig.add_bar(x=part["band"].str.replace(r"^\d: ", "", regex=True), y=part["late_rate"], row=1, col=col,
                    marker_color=LATE_COLOR, customdata=part[["orders", "late_orders"]],
                    hovertemplate="%{x}<br>late rate %{y:.1%}<br>%{customdata[1]:,} late of %{customdata[0]:,} orders<extra></extra>")
    fig.update_yaxes(tickformat=".0%", title_text="late rate per order")
    fig.update_layout(showlegend=False, height=380, margin={"t": 40})
    return fig


def route_frame(state, year, exclude_carnival, min_orders):
    """Chart 2 data: seller-state to customer-state routes with seller share of late orders."""
    where, params = where_clause(state, year, exclude_carnival)
    sql = ("SELECT seller_state, customer_state, SUM(orders) AS orders, SUM(late_orders) AS late_orders, "
           "SUM(seller_late_orders) AS seller_late, SUM(carrier_late_orders) AS carrier_late "
           "FROM vw_chart_route_cause" + where +
           " GROUP BY seller_state, customer_state HAVING SUM(orders) >= ? AND SUM(seller_late_orders) + SUM(carrier_late_orders) > 0")
    df = read(sql, params + [int(min_orders)])
    df["seller_share"] = df["seller_late"] / (df["seller_late"] + df["carrier_late"])
    return df


def route_figure(state, year, exclude_carnival, min_orders):
    df = route_frame(state, year, exclude_carnival, min_orders)
    if df.empty:
        return empty_figure()
    grid = df.pivot(index="seller_state", columns="customer_state", values="seller_share")
    counts = df.pivot(index="seller_state", columns="customer_state", values="late_orders")
    fig = go.Figure(go.Heatmap(
        z=grid.values, x=grid.columns, y=grid.index, zmin=0, zmax=1, colorscale="RdYlBu_r",
        colorbar={"title": "seller share<br>of late orders"}, customdata=counts.values,
        hovertemplate="seller in %{y} to customer in %{x}<br>seller share %{z:.0%}<br>%{customdata} late orders<extra></extra>"))
    fig.update_layout(height=520, margin={"t": 30}, xaxis_title="customer state", yaxis_title="seller state")
    return fig


def category_frame(state, year, exclude_carnival, min_orders, top=12):
    """Chart 3 data: per category, review score and revenue for late and on-time orders."""
    where, params = where_clause(state, year, exclude_carnival)
    sql = ("SELECT category, is_late, SUM(orders) AS orders, SUM(review_orders) AS review_orders, "
           "SUM(review_sum) AS review_sum, SUM(revenue_usd) AS revenue_usd "
           "FROM vw_chart_category_cost" + where + " GROUP BY category, is_late HAVING SUM(orders) >= ?")
    df = read(sql, params + [int(min_orders)])
    df["avg_review"] = df["review_sum"] / df["review_orders"].where(df["review_orders"] > 0)
    # keep only categories that have BOTH late and on-time rows, then the biggest by late revenue
    both = df.groupby("category")["is_late"].nunique()
    df = df[df["category"].isin(both[both == 2].index)]
    if df.empty:
        return df
    best = df[df["is_late"] == 1].nlargest(top, "revenue_usd")["category"]
    return df[df["category"].isin(best)]


def category_figure(state, year, exclude_carnival, min_orders):
    df = category_frame(state, year, exclude_carnival, min_orders)
    if df.empty:
        return empty_figure()
    order = df[df["is_late"] == 1].sort_values("revenue_usd", ascending=False)["category"]
    fig = make_subplots(rows=2, cols=1, subplot_titles=("Average review score (1-5)", "Revenue in US$ (item prices)"),
                        vertical_spacing=0.22)
    for flag, name, color in ((0, "on time", ONTIME_COLOR), (1, "late", LATE_COLOR)):
        part = df[df["is_late"] == flag].set_index("category").reindex(order)
        for row, column in ((1, "avg_review"), (2, "revenue_usd")):
            fig.add_bar(x=part.index, y=part[column], name=name, marker_color=color, row=row, col=1,
                        legendgroup=name, showlegend=(row == 1))
    fig.update_layout(barmode="group", height=620, margin={"t": 50}, legend={"orientation": "h", "y": 1.08})
    fig.update_yaxes(range=[1, 5], row=1, col=1)
    return fig


# ---------------------------------------------------------------- the app

def build_layout():
    states, years = filter_choices()
    return html.Div([
        html.H1("Late deliveries in Brazilian e-commerce (Olist, 2016-2018)"),
        html.P("Which categories, sellers and customer states are late most often, and why? "
               "Late = delivered after the estimated date (dates compared, not times). Delivered orders only."),
        html.Div([
            html.Div([html.Label("Customer state"),
                      dcc.Dropdown(id="state", options=[{"label": s, "value": s} for s in states],
                                   placeholder="All states", clearable=True,
                                   value=os.environ.get("DEFAULT_STATE") or None)], className="control"),
            html.Div([html.Label("Purchase year"),
                      dcc.Dropdown(id="year", options=[{"label": str(y), "value": y} for y in years],
                                   placeholder="All years", clearable=True,
                                   value=int(os.environ["DEFAULT_YEAR"]) if os.environ.get("DEFAULT_YEAR") else None)], className="control"),
            html.Div([html.Label("Carnival"),
                      dcc.Checklist(id="carnival", options=[{"label": " Leave out orders with Carnival in their window", "value": "x"}],
                                    value=[])], className="control"),
            html.Div([html.Label("Minimum orders per bar, route or category"),
                      dcc.Slider(id="min_orders", min=1, max=500, value=30, step=None,
                                 marks={1: "1", 10: "10", 30: "30", 100: "100", 250: "250", 500: "500"})],
                     className="control wide"),
        ], className="controls"),
        html.Div(id="kpis", className="kpis"),
        html.H3("1. Late rate by distance and by freight share"),
        dcc.Graph(id="chart_bands"), html.Div(NOTE, className="note"),
        html.H3("2. Who causes the delay: seller or carrier? (share of late orders caused by the seller, by route)"),
        dcc.Graph(id="chart_routes"),
        html.Div(NOTE + " Cause rule: seller if the parcel reached the carrier after the shipping limit date, otherwise carrier "
                        "(we have no promised transit time).", className="note"),
        html.H3("3. Review score and revenue, late vs on-time orders, by category"),
        dcc.Graph(id="chart_categories"),
        html.Div(NOTE + " Revenue is revenue on late orders, not revenue lost.", className="note"),
    ], className="page")


CSS = """
body { font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f7f7f5; color: #222; }
.page { max-width: 1200px; margin: 0 auto; padding: 16px 20px 40px; }
.controls { display: flex; flex-wrap: wrap; gap: 16px; background: #fff; padding: 12px 16px; border-radius: 8px; }
.control { min-width: 200px; flex: 1; } .control.wide { flex: 2; min-width: 320px; }
.kpis { display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.kpi { background: #fff; border-radius: 8px; padding: 10px 14px; flex: 1; min-width: 170px; }
.kpi-title { font-size: 12px; color: #666; } .kpi-value { font-size: 26px; font-weight: 600; } .kpi-small { font-size: 11px; color: #777; }
.note { font-size: 12px; color: #666; margin: 0 0 20px; }
"""


def create_app():
    app = Dash(__name__, title="Olist late deliveries")
    app.index_string = app.index_string.replace("</head>", f"<style>{CSS}</style></head>")
    app.layout = build_layout

    inputs = [Input("state", "value"), Input("year", "value"),
              Input("carnival", "value"), Input("min_orders", "value")]

    def unpack(state, year, carnival, min_orders):
        return state, year, bool(carnival), min_orders or 1

    @app.callback(Output("kpis", "children"), inputs)
    def update_kpis(state, year, carnival, min_orders):
        return kpi_cards(header_numbers(*unpack(state, year, carnival, min_orders)[:3]))

    @app.callback(Output("chart_bands", "figure"), inputs)
    def update_bands(state, year, carnival, min_orders):
        return distance_freight_figure(*unpack(state, year, carnival, min_orders))

    @app.callback(Output("chart_routes", "figure"), inputs)
    def update_routes(state, year, carnival, min_orders):
        return route_figure(*unpack(state, year, carnival, min_orders))

    @app.callback(Output("chart_categories", "figure"), inputs)
    def update_categories(state, year, carnival, min_orders):
        return category_figure(*unpack(state, year, carnival, min_orders))

    return app


if __name__ == "__main__":
    if not db_path().exists():
        sys.exit(f"Warehouse not found at {db_path()}. Run `python run_pipeline.py` first.")
    create_app().run(debug=False, port=int(os.environ.get("PORT", 8050)))
