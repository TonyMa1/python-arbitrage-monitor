import sys
import asyncio
import click

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Header, Footer, Static, Log
from textual.containers import ScrollableContainer
from spread2 import TickerSpreadMonitor, OrderbookSpreadMonitor

# Add this before any async code runs
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class TickerSpreadPanel(Static):
    def compose(self) -> ComposeResult:
        yield DataTable()

    async def load_data(self):
        top_n = self.app.monitor_params['top_n']
        params = self.app.monitor_params.copy()
        del params["top_n"]
        monitor = TickerSpreadMonitor(**params)

        table = self.query_one(DataTable)
        try:
            await monitor.load_markets()
            monitor.start()
            while True:
                await asyncio.sleep(1)
                table.clear()
                data = monitor.top(top_n)
                for i, row in enumerate(data):
                    # Format the 'spread' value here!
                    spread_str = f"{row['spread']:.8f}"  # Format to 8 decimal places

                    table.add_row(
                        i,
                        row['pair_name'],
                        f'{(row["spread_pct"] * 100):.4f}%',  # Consistent .4f formatting
                        spread_str,  # Use the formatted string
                        f"{row['price_a']:.8f}",  # Format prices
                        f"{row['price_b']:.8f}",  # Format prices
                        f'{row["elapsed_time_a"]:.2f}ms',
                        f'{row["elapsed_time_b"]:.2f}ms',
                    )
        except BaseException as e:
            await monitor.stop()
            self.app.log_exception(e)

    async def on_mount(self):
        self.query_one(DataTable).add_columns(
            "No.",
            "Pair",
            "Spread (%)",
            "Spread",
            "Last Price (A)",
            "Last Price (B)",
            "Latency (A)",
            "Latency (B)"
        )
        asyncio.create_task(self.load_data())


class OrderbookSpreadPanel(Static):
    def compose(self) -> ComposeResult:
        yield DataTable()

    async def load_data(self):
        top_n = self.app.monitor_params['top_n']

        params = self.app.monitor_params.copy()
        del params["top_n"]
        monitor = OrderbookSpreadMonitor(**params)

        table = self.query_one(DataTable)
        try:
            await monitor.load_markets()
            monitor.start()
            while True:
                await asyncio.sleep(1)
                table.clear()
                data = monitor.top(top_n)
                for i, row in enumerate(data):
                    # Format spread percentages and other float values
                    table.add_row(
                        i,
                        row['pair_name'],
                        f"{row['spread_pct'] * 100:.4f}%",
                        f"{row['buy_a_sell_b_spread_pct'] * 100:.4f}%",
                        f"{row['buy_b_sell_a_spread_pct'] * 100:.4f}%",
                        f"{row['bid_price_a']:.8f}/{row['bid_volume_a']:.2f}",  # Format prices/volumes
                        f"{row['ask_price_a']:.8f}/{row['ask_volume_a']:.2f}",
                        f"{row['bid_price_b']:.8f}/{row['bid_volume_b']:.2f}",
                        f"{row['ask_price_b']:.8f}/{row['ask_volume_b']:.2f}",
                        f"{row['elapsed_time_a']:.2f}ms/{row['elapsed_time_b']:.2f}ms",
                    )
        except BaseException as e:
            await monitor.stop()
            self.app.log_exception(e)

    async def on_mount(self):
        self.query_one(DataTable).add_columns(
            "No.",
            "Pair",
            "Spread",
            "Long A Short B",
            "Long B Short A",
            "Bid Price/Vol (A)",
            "Ask Price/Vol (A)",
            "Bid Price/Vol (B)",
            "Ask Price/Vol (B)",
            "Latency (A/B)",
        )
        asyncio.create_task(self.load_data())


class MonitorApp(App):

    CSS = """
        #scroll {
            width: 100%;
            height: 100%;
            overflow-x: auto;
            overflow-y: auto;
        }
        #content {
            width: 1000vw;
            height: 1000vh;
            min-width: 100vw;
            min-height: 100vh;
        }
        """

    def __init__(self, monitor_panel, monitor_params):
        self.TITLE = f"Arbitrage Monitor: A-{monitor_params['market_a']} B-{monitor_params['market_b']}"

        super().__init__()
        self.monitor_panel = monitor_panel
        self.monitor_params = monitor_params

    def create_monitor_panel(self, id):
        if self.monitor_panel == "ticker":
            return TickerSpreadPanel(id=id)
        elif self.monitor_panel == "orderbook":
            return OrderbookSpreadPanel(id=id)
        else:
            raise ValueError("Invalid monitor panel type")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Log()
        with ScrollableContainer(id="scroll"):
            yield self.create_monitor_panel(id="content")


@click.command()
@click.option("--monitor-panel",
              type=click.Choice(['orderbook', 'ticker'], case_sensitive=False),
              default="ticker",
              required=True,
              help="Monitoring panel type (orderbook/ticker). "
                   "Determines the data source and display format.  "
                   "'ticker' uses last trade prices, while 'orderbook' "
                   "uses the top bid/ask from the order book.")
@click.option("--market-a",
              type=click.STRING,
              default='binance.spot',
              required=True,
              help="Market A structure: exchange.type[.subtype], e.g. binance.spot, okx.future.linear. "
                   "This specifies the first exchange and market type to monitor for arbitrage opportunities.  "
                   "The format is crucial for correct data retrieval.")
@click.option("--market-b",
              type=click.STRING,
              default='okx.swap.linear',
              required=True,
              help="Market B structure: exchange.type[.subtype], e.g. binance.spot, okx.future.linear. "
                   "This specifies the second exchange and market type.  "
                   "It must be different from Market A.  The same format rules apply as for '--market-a'.")
@click.option("--quote-currency",
              default="USDT",
              show_default=True,
              help="Base quote currency. This is the currency against which trading pairs are quoted (e.g., USDT, USD, BTC).  "
                   "The script will only consider pairs that use this quote currency on *both* exchanges, unless --symbols is used.")
@click.option("--symbols",
              default=None,
              help="Filter symbols, comma-separated (e.g. BTC-USDT,ETH-USDT).  "
                   "If provided, this *overrides* the `--quote-currency` option.  "
                   "The script will *only* monitor the specified trading pairs. The pairs should be in the format used by the exchanges (typically Base-Quote).")
@click.option("--topn",
              type=int,
              default=20,
              show_default=True,
              help="Number of top items to monitor.  This limits the displayed results to the 'n' pairs with the largest spread percentage.")
def main(monitor_panel, market_a, market_b, quote_currency, symbols, topn):
    symbols_set = set(symbols.split(',')) if symbols else None
    monitor_params = {
        'market_a': market_a,
        'market_b': market_b,
        'quote_currency': quote_currency,
        'symbols': symbols_set,
        'top_n': topn,
    }
    MonitorApp(monitor_panel, monitor_params=monitor_params).run()


if __name__ == "__main__":
    main()