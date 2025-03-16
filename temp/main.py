import sys
import asyncio
import os
import logging
from typing import Dict, Any

import click
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Header, Footer, Static, Log
from textual.containers import ScrollableContainer

from spread import TickerSpreadMonitor

# Setup logging with stdout and file output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('arbitrage_monitor.log')
    ]
)
logger = logging.getLogger('arbitrage_monitor')

# Fix for Windows event loop issues
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Default monitor configuration
DEFAULT_CONFIG = {
    "batch_size": 50,
    "thread_pool_size": 4,
    "update_interval_ms": 500,
    "max_data_age_ms": 60000,
    "backoff_initial": 1,
    "backoff_max": 30,
    "backoff_factor": 2,
}


def get_config() -> Dict[str, Any]:
    """Load configuration with optional environment variable overrides."""
    config = DEFAULT_CONFIG.copy()
    
    # Apply environment variable overrides if present
    for key in config:
        env_key = f"ARBITRAGE_MONITOR_{key.upper()}"
        if env_key in os.environ:
            # Convert values to appropriate type based on default
            if isinstance(config[key], int):
                config[key] = int(os.environ[env_key])
            elif isinstance(config[key], float):
                config[key] = float(os.environ[env_key])
            elif isinstance(config[key], bool):
                config[key] = os.environ[env_key].lower() in ('true', 'yes', '1')
            else:
                config[key] = os.environ[env_key]
    
    return config


def format_significant_figures(value, sig_figs=3):
    """Format number with specified significant figures."""
    if value == 0:
        return "0.000"
    
    # Calculate power of 10 for decimal point positioning
    power = 0
    temp = abs(value)
    if temp >= 1:
        while temp >= 10:
            temp /= 10
            power += 1
        decimal_places = sig_figs - 1
        return f"{value:.{decimal_places}f}"
    else:
        # Handle numbers less than 1 by counting leading zeros
        while temp < 1:
            temp *= 10
            power -= 1
        decimal_places = sig_figs + abs(power) - 1
        return f"{value:.{decimal_places}f}"


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
            # Display loading status while fetching market data
            table.add_row("Loading markets...", "", "", "", "", "", "", "")
            await monitor.load_markets()
            table.clear()
            
            # Start the background monitor
            monitor.start()
            
            # Cache previous data to reduce UI redraws
            last_data = []
            while True:
                try:
                    # Update every 0.5s for responsive UI
                    await asyncio.sleep(0.5)
                    data = monitor.top(top_n)
                    
                    # Only redraw table when data has changed
                    if data != last_data:
                        table.clear()
                        for i, row in enumerate(data):
                            table.add_row(
                                i,
                                row['pair_name'],
                                f'{(row["spread_pct"] * 100):.4f}%',
                                format_significant_figures(row['spread']),
                                format_significant_figures(row['price_a']),
                                format_significant_figures(row['price_b']),
                                f'{row["elapsed_time_a"]:.2f}ms',
                                f'{row["elapsed_time_b"]:.2f}ms',
                            )
                        last_data = data.copy()  # Store for next comparison
                except asyncio.CancelledError:
                    # Clean exit on cancellation
                    break
        except BaseException as e:
            # Ensure monitor is stopped on error
            await monitor.stop()
            logging.error(f"Error in load_data: {e}")
        finally:
            # Always stop monitor before exiting
            try:
                await monitor.stop()
            except Exception:
                pass

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

    def __init__(self, monitor_params):
        self.TITLE = (
            f"Arbitrage Monitor: A-{monitor_params['market_a']} "
            f"B-{monitor_params['market_b']}"
        )
        super().__init__()
        self.monitor_params = monitor_params
        
    def log_exception(self, exception):
        """Log exception to error stream."""
        logging.error(f"Error: {exception}")
        
    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Log()
        with ScrollableContainer(id="scroll"):
            yield TickerSpreadPanel(id="content")


@click.command()
@click.option(
    "--market-a",
    type=click.STRING,
    default='binance.spot',
    required=True,
    help="Market A structure: exchange.type[.subtype], e.g. binance.spot, "
         "okx.future.linear. This specifies the first exchange and market type "
         "to monitor for arbitrage opportunities. The format is crucial for "
         "correct data retrieval."
)
@click.option(
    "--market-b",
    type=click.STRING,
    default='okx.swap.linear',
    required=True,
    help="Market B structure: exchange.type[.subtype], e.g. binance.spot, "
         "okx.future.linear. This specifies the second exchange and market type. "
         "It must be different from Market A. The same format rules apply as for "
         "'--market-a'."
)
@click.option(
    "--quote-currency",
    default="USDT",
    show_default=True,
    help="Base quote currency. This is the currency against which trading pairs "
         "are quoted (e.g., USDT, USD, BTC). The script will only consider pairs "
         "that use this quote currency on *both* exchanges, unless --symbols is used."
)
@click.option(
    "--symbols",
    default=None,
    help="Filter symbols, comma-separated (e.g. BTC-USDT,ETH-USDT). "
         "If provided, this *overrides* the `--quote-currency` option. "
         "The script will *only* monitor the specified trading pairs. The pairs "
         "should be in the format used by the exchanges (typically Base-Quote)."
)
@click.option(
    "--topn",
    type=int,
    default=20,
    show_default=True,
    help="Number of top items to monitor. This limits the displayed results to "
         "the 'n' pairs with the largest spread percentage."
)
def main(market_a, market_b, quote_currency, symbols, topn):
    # Validate market parameters
    if market_a == market_b:
        print("Error: market_a and market_b must be different")
        sys.exit(1)
        
    # Parse and validate symbols
    symbols_set = None
    if symbols:
        symbols_set = set(s.strip() for s in symbols.split(',') if s.strip())
        if not symbols_set:
            print("Error: Invalid symbols format")
            sys.exit(1)
    
    monitor_params = {
        'market_a': market_a,
        'market_b': market_b,
        'quote_currency': quote_currency,
        'symbols': symbols_set,
        'top_n': topn,
    }
    MonitorApp(monitor_params=monitor_params).run()


if __name__ == "__main__":
    main()