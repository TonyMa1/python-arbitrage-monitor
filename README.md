# Python Arbitrage Monitor

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Description

This project is a Python application that monitors cryptocurrency prices across two different exchanges (Market A and Market B) to identify potential arbitrage opportunities. It uses the `ccxt.pro` library for real-time data streaming and provides a Textual-based user interface to display the top N arbitrage spreads. The monitor tracks spreads based on the last traded price (ticker).

**Key Features:**

*   **Real-time Monitoring:** Uses websockets to receive live price updates from exchanges.
*   **Dual Exchange Support:** Monitors two exchanges simultaneously.
*   **Ticker Spreads:** Calculates spreads using last trade prices.
*   **Configurable:** Allows specifying exchanges, market types, quote currency, specific symbols, and the number of top spreads to display.
*   **Textual UI:** Presents results in a user-friendly, interactive table.
*   **Latency Measurement:** Displays the latency of data retrieval from each exchange.
*   **Threaded Calculations:** Uses a thread pool to perform calculations without blocking the main event loop, ensuring responsiveness.

## Project Origin

This project was developed as part of the MFIN7037 Quantitative Trading course in the Master of Financial Technology program at The University of Hong Kong (HKU) in 2025.

## Requirements

*   **Python 3.11.8 (Recommended)**
*   `ccxt>=4.4.62`
*   `textual>=2.1.2`
*    `click`
*   `asyncio`
*   `textual-dev>=1.7.0`  *(Development Dependency)*
*   `textual-serve>=1.1.1` *(Development Dependency)*

It is strongly recommended to use a virtual environment to manage these dependencies.

## Installation

1.  **Clone Repository:**

    ```bash
    git clone https://github.com/TonyMa1/python-arbitrage-monitor.git
    cd python-arbitrage-monitor
    ```

2.  **Create and Activate a Virtual Environment (Recommended):**

    Using `venv` (recommended for Python 3.7+ , I use a python3.11.8 on my arm64 laptop):

    ```bash
    python3 -m venv venv  # Create the virtual environment
    source venv/bin/activate  # Activate on macOS/Linux
    venv\Scripts\activate  # Activate on Windows
    ```

3.  **Install Dependencies:**

    ```bash
    # Recommended: Use uv for faster, more reliable dependency installation
    pip install uv
    uv pip install -r requirements.txt
    
    # Alternative: Standard pip installation
    pip install -r requirements.txt
    ```

## Usage

The script is run from the command line using `click`. Here's the basic usage:

```bash
python main1.py --market-a <market_a> --market-b <market_b> [options]
```

**Options:**

*   `--market-a`:  Specify the first exchange and market type (required).  Format: `exchange.type[.subtype]`.  Examples:
    *   `binance.spot`
    *   `okx.future.linear`

*   `--market-b`:  Specify the second exchange and market type (required).  Must be different from Market A.  Format: `exchange.type[.subtype]`.  Examples:
    *   `binance.spot`
    *   `okx.swap.linear`

*   `--quote-currency`:  The quote currency to use for filtering trading pairs (default: `USDT`). The script will only consider pairs using this quote currency on both exchanges, unless `--symbols` is used.

*   `--symbols`:  A comma-separated list of specific trading pairs to monitor (e.g., `BTC-USDT,ETH-USDT`).  If provided, this *overrides* the `--quote-currency` option.

*   `--topn`: The number of top arbitrage opportunities to display (default: 20).

**Example Usage:**

To monitor ticker spreads between Binance spot and OKX linear swaps for the top 10 pairs with USDT as the quote currency:

```bash
python main1.py --market-a binance.spot --market-b okx.swap.linear --topn 10
```

## How it Works (Technical Details)

This section provides a more in-depth explanation of the code's architecture and functionality.

1.  **Exchange Connections:** The `SpreadMonitorBase` class handles creating and managing connections to the two specified exchanges using `ccxt.pro`. It uses asynchronous methods (`asyncio`) to handle real-time data streams efficiently.

2.  **Market Loading:**  The `load_markets()` method retrieves market data from both exchanges and filters trading pairs based on the provided `quote_currency` or `symbols`.  It builds an internal `symbol_map` to efficiently look up pairs for spread calculations.

3.  **Data Monitoring:** The `TickerSpreadMonitor` class implements the actual monitoring logic. It uses `watch_tickers` to subscribe to real-time data streams from the exchanges.

4.  **Spread Calculation:**  The `process_ticker` method receives data from the exchanges. It updates the `pair_data` dictionary with the latest prices and timestamps. The `calculate_spread` method (which uses a thread pool for CPU-bound calculations) then computes the spread percentage.

5.  **Textual UI:** The `MonitorApp` class (using the `textual` library) creates a dynamic table to display the top N arbitrage opportunities, sorted by spread percentage. The `TickerSpreadPanel` class defines the specific table layout and data formatting.

6.  **Latency Handling:**  The `sync_time` method periodically synchronizes the local clock with the exchange servers to estimate latency.  This latency is used to calculate more accurate elapsed times for price updates.

7.  **Error Handling:**  The code includes `try...except` blocks to handle potential network errors and other exceptions that might occur during data retrieval or processing.

## Contributing

Contributions are welcome! If you find a bug or have a feature request, please open an issue on GitHub.  If you'd like to contribute code, please fork the repository and submit a pull request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

