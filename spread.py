import os
import time
import asyncio
import traceback
import itertools
from typing import Dict, Tuple, List, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import ccxt.pro as ccxtpro


def get_proxy_settings():
    """Return proxy configuration for exchange connections."""
    return {
        "enableRateLimit": True,
        "proxies": {
            "http": os.getenv("http_proxy"),
            "https": os.getenv("https_proxy"),
        },
        "aiohttp_proxy": os.getenv("http_proxy"),
        "ws_proxy": os.getenv("http_proxy"),
    }


def create_exchange(name):
    """Create and configure exchange instance with proxy settings."""
    return getattr(ccxtpro, name)(get_proxy_settings())


class SpreadMonitorBase:
    """Base class for arbitrage spread monitoring between exchanges.
    
    Handles the core functionality for connecting to exchanges, mapping symbols,
    and monitoring price differences for potential arbitrage opportunities.
    """
    
    def __init__(
        self, 
        market_a: str, 
        market_b: str, 
        symbols: Set[str] = None, 
        quote_currency: str = "USDT"
    ):
        """Initialize the spread monitor with exchange configurations.
        
        Args:
            market_a: First market in format 'exchange.type[.subtype]'
            market_b: Second market in format 'exchange.type[.subtype]'
            symbols: Optional set of specific symbols to monitor
            quote_currency: Quote currency to filter pairs (e.g., 'USDT')
            
        The monitor connects to two exchanges and tracks price differences
        between them for potential arbitrage opportunities.
        """
        # Parse market strings into components
        self.exchange_a_name, self.type_a, self.subtype_a = self.parse_market(market_a)
        self.exchange_b_name, self.type_b, self.subtype_b = self.parse_market(market_b)

        # Create exchange connections
        self.exchange_a: ccxtpro.Exchange = create_exchange(self.exchange_a_name)
        self.exchange_b: ccxtpro.Exchange = create_exchange(self.exchange_b_name)

        self.symbols: Set[str] = symbols if symbols is not None else set()
        self.quote_currency: str = quote_currency

        # Data structures to store market info and monitoring state
        self.symbol_map: Dict[str, Dict] = defaultdict(dict)
        self.pair_data: Dict[str, Dict] = {}
        self.monitor_tasks: List[asyncio.Task] = []
        self.running: bool = False

        # Track exchange latencies for more accurate timing
        self.latencies: Dict[str, Dict] = defaultdict(dict)
        self._executor = ThreadPoolExecutor(max_workers=4)

    async def sync_time(self, exchange: ccxtpro.Exchange):
        """Synchronize time with exchange and calculate latency.
        
        Continuously checks the time difference between local system
        and exchange server to account for network delays in price data.
        """
        while self.running:
            try:
                # Measure round-trip time to exchange
                start_time = time.time() * 1000
                server_time = await exchange.fetch_time()
                end_time = time.time() * 1000

                # Calculate network stats
                rtt = end_time - start_time
                latency = rtt / 2
                time_diff = end_time - (server_time + latency)

                self.latencies[exchange.name.lower()] = {
                    "latency": latency,
                    "time_diff": time_diff,
                }
            except (asyncio.CancelledError, asyncio.exceptions.CancelledError):
                break
            except Exception as e:
                print(f"Error in sync_time ({exchange.name}): {e}")
                await asyncio.sleep(5)

    def parse_market(self, market: str) -> Tuple[str, str, str]:
        """Parse market string into exchange, type and subtype components.
        
        Takes strings like 'binance.spot' or 'ftx.future.linear' and splits them
        into their components for exchange configuration.
        """
        market_params = market.split(".")
        if len(market_params) == 2:
            exchange_name, type_ = market_params
            return exchange_name, type_, None
        elif len(market_params) == 3:
            exchange_name, type_, subtype = market_params
            return exchange_name, type_, subtype
        else:
            raise ValueError(
                "Market parameter must match format: "
                "<exchange>.<type> or <exchange>.<type>.<subtype>"
            )

    async def load_markets(self):
        """Load market data from both exchanges and build symbol mapping.
        
        Fetches available markets from exchanges and creates mappings
        between symbols on different exchanges for the same asset pairs.
        """
        # Load markets data from both exchanges concurrently
        await asyncio.gather(
            self.exchange_a.load_markets(),
            self.exchange_b.load_markets()
        )

        def format_markets(markets: Dict, type_: str, subtype: str) -> Dict[Tuple[str, str], List[str]]:
            """Filter markets by type, subtype and quote currency.
            
            Args:
                markets: Dictionary of markets from the exchange
                type_: Market type (e.g., 'spot', 'future', 'swap')
                subtype: Market subtype (e.g., 'linear', 'inverse')
                
            Returns:
                Dictionary mapping (base, quote) currency pairs to symbols
            """
            new_markets = defaultdict(list)
            for m in markets.values():
                # Only include markets matching our criteria
                if (
                    m["type"] == type_
                    and (subtype is None or m[subtype])
                    and (
                        m["quote"] == self.quote_currency
                        or (self.quote_currency is None and 
                            f"{m['base']}-{m['quote']}" in self.symbols)
                    )
                ):
                    new_markets[(m["base"], m["quote"])].append(m["symbol"])
            return new_markets

        # Format and filter markets for both exchanges
        markets_a = format_markets(self.exchange_a.markets, self.type_a, self.subtype_a)
        markets_b = format_markets(self.exchange_b.markets, self.type_b, self.subtype_b)

        # Find common currency pairs available on both exchanges
        keys = set(markets_a.keys()) & set(markets_b.keys())
        pairs = [
            {
                "base": base,
                "quote": quote,
                "symbols_a": markets_a[(base, quote)],
                "symbols_b": markets_b[(base, quote)],
            }
            for base, quote in keys
        ]
        self.symbol_map = await self._build_symbol_map_async(pairs)

    async def _build_symbol_map_async(self, pairs: List[Dict]) -> Dict[str, Dict]:
        """Build symbol mapping in a separate thread to avoid blocking."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._build_symbol_map, pairs)

    def _build_symbol_map(self, pairs: List[Dict]) -> Dict[str, Dict]:
        """Create mapping between exchange symbols and trading pairs.
        
        Builds a cross-reference structure so we can match symbols
        across exchanges and track the same asset pair on both.
        """
        symbol_map: Dict[str, Dict] = {"a": {}, "b": {}}
        for pair in pairs:
            # Create all possible combinations of symbols between exchanges
            for symbol_a, symbol_b in itertools.product(
                pair["symbols_a"], pair["symbols_b"]
            ):
                pair_name = f"{symbol_a}-{symbol_b}"
                symbol_map["a"].setdefault(
                    symbol_a, {"index": "a", "pair_names": []}
                )["pair_names"].append(pair_name)
                symbol_map["b"].setdefault(
                    symbol_b, {"index": "b", "pair_names": []}
                )["pair_names"].append(pair_name)
        return symbol_map

    async def monitor(self, exchange, index, symbols):
        """Abstract method to be implemented by subclasses."""
        raise NotImplementedError("Method is not implemented")

    def top(self, n: int) -> List[Dict]:
        """Return top n pairs sorted by spread percentage.
        
        Useful for identifying the most profitable arbitrage opportunities.
        """
        data = list(self.pair_data.values())
        return sorted(data, key=lambda x: x["spread_pct"], reverse=True)[:n]

    def start(self):
        """Start monitoring tasks for both exchanges.
        
        Launches background tasks to monitor price feeds and
        track exchange time differences.
        """
        self.running = True
        # Process symbols in batches to avoid excessive concurrent connections
        batch_size = 50
        a_symbols = list(self.symbol_map["a"].keys())
        b_symbols = list(self.symbol_map["b"].keys())

        # Create time sync tasks
        self.monitor_tasks.extend([
            asyncio.create_task(self.sync_time(self.exchange_a)),
            asyncio.create_task(self.sync_time(self.exchange_b)),
        ])

        # Create monitoring tasks for first exchange, in batches
        self.monitor_tasks.extend([
            asyncio.create_task(self.monitor(
                self.exchange_a, "a", a_symbols[i:i+batch_size]
            ))
            for i in range(0, len(a_symbols), batch_size)
        ])
        # Create monitoring tasks for second exchange, in batches
        self.monitor_tasks.extend([
            asyncio.create_task(self.monitor(
                self.exchange_b, "b", b_symbols[i:i+batch_size]
            ))
            for i in range(0, len(b_symbols), batch_size)
        ])

    async def stop(self):
        """Stop all monitoring tasks and clean up resources.
        
        This method gracefully shuts down all running tasks:
        1. Sets running flag to False to signal tasks to exit
        2. Cancels all pending tasks
        3. Closes exchange connections
        4. Shuts down the thread executor
        """
        import logging  # Import at top level is preferred, but keeping here for minimal changes
        
        self.running = False
        for task in self.monitor_tasks:
            if not task.done():
                task.cancel()
                
        try:
            await asyncio.gather(*self.monitor_tasks, return_exceptions=True)
        except (asyncio.CancelledError, Exception) as e:
            logging.debug(f"Task cancellation during stop: {e}")
            
        try:
            if hasattr(self, 'exchange_a') and self.exchange_a:
                await self.exchange_a.close()
            if hasattr(self, 'exchange_b') and self.exchange_b:
                await self.exchange_b.close()
        except Exception as e:
            logging.debug(f"Error closing exchanges: {e}")
            
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)


class TickerSpreadMonitor(SpreadMonitorBase):
    """Monitors price spreads between exchanges using ticker data.
    
    Implements the monitor method using exchange ticker feeds, which provide
    regular updates on current prices across all tracked symbols.
    """
    
    async def monitor(self, exchange: ccxtpro.Exchange, index: str, symbols: List[str]):
        """Watch and process ticker updates for specified symbols.
        
        Args:
            exchange: The CCXT exchange instance to monitor
            index: Exchange identifier ('a' or 'b')
            symbols: List of trading symbols to watch
        """
        exchange_name = exchange.name.lower()
        while self.running:
            try:
                # Get ticker updates from exchange websocket
                tickers = await exchange.watch_tickers(symbols)
                # Process each ticker concurrently
                await asyncio.gather(*[
                    self.process_ticker(
                        symbol, 
                        ticker, 
                        index, 
                        self.latencies[exchange_name].get("time_diff", 0)
                    )
                    for symbol, ticker in tickers.items()
                ])
            except asyncio.CancelledError:
                break
            except ccxtpro.NetworkError as e:
                # Handle network issues with simple backoff
                print(f"Network error ({index}): {e}")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Exception ({index}): {traceback.format_exc()}")
                await asyncio.sleep(5)

    async def process_ticker(
        self, symbol: str, ticker: Dict, index: str, time_diff: float
    ):
        """Process a ticker update and update pair data.
        
        Takes new price information from an exchange and updates
        our internal state for that asset pair.
        """
        # Skip if we're not tracking this symbol
        if symbol not in self.symbol_map[index]:
            return

        # Get all pairs this symbol is part of
        pair_names = self.symbol_map[index][symbol]["pair_names"]

        for pair_name in pair_names:
            # Initialize pair data if needed
            data = self.pair_data.setdefault(pair_name, {
                "pair_name": pair_name,
                "spread": 0.0,
                "spread_pct": 0.0,
                "spread_after_fees_pct": 0.0,
                "price_a": 0.0,
                "price_b": 0.0,
                "elapsed_time_a": 0.0,
                "elapsed_time_b": 0.0,
            })
            # Update with latest price info
            data[f"price_{index}"] = ticker["last"]
            data[f"elapsed_time_{index}"] = time.time() * 1e3 - (ticker["timestamp"] + time_diff)
            await self.calculate_spread(pair_name)

    async def calculate_spread(self, pair_key: str):
        """Calculate price spread in a separate thread to avoid blocking.
        
        Offloads the spread calculation to avoid blocking the main async loop,
        which needs to keep processing incoming price updates.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._calculate_spread_sync, pair_key)

    def _calculate_spread_sync(self, pair_key: str):
        """Calculate absolute and percentage price spread between exchanges.
        
        Computes the price difference and percentage spread, which indicates 
        the potential profit from arbitrage (before fees and slippage).
        """
        data = self.pair_data[pair_key]
        try:
            if data["price_a"] and data["price_b"]:
                # Find price difference and calculate percentage
                min_price = min(data["price_a"], data["price_b"])
                spread = abs(data["price_a"] - data["price_b"])
                data["spread"] = spread
                data["spread_pct"] = spread / min_price if min_price != 0 else 0
                # Calculate spread after fees and slippage (0.4% deduction)
                data["spread_after_fees_pct"] = max(0, data["spread_pct"] - 0.004)
        except TypeError as e:
            print(f"Calculate spread error for {pair_key}: {e}")
            data["spread_pct"] = 0
            data["spread_after_fees_pct"] = 0