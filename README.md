# Python Arbitrage Monitor

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![CCXT](https://img.shields.io/badge/ccxt-4.4.62+-green.svg)](https://github.com/ccxt/ccxt)
[![Textual](https://img.shields.io/badge/textual-2.1.2+-purple.svg)](https://github.com/Textualize/textual)

A real-time cryptocurrency arbitrage monitoring tool that tracks price differences between exchanges to identify trading opportunities.

## Features

- **Live Monitoring**: Stream real-time price data via websockets
- **Multi-Exchange**: Compare prices across different exchanges simultaneously
- **Market Type Support**: Monitor spot, futures, and swap markets
- **Configurable**: Customize trading pairs, quote currencies, and display options
- **Performance**: Threaded calculations ensure responsive UI during data processing
- **Latency Tracking**: Monitor data freshness with exchange latency measurements
- **Terminal UI**: Clean interface built with Textual for easy visualization

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Basic usage (compare Binance spot with OKX linear swaps)
python main.py --market-a binance.spot --market-b okx.swap.linear
```

## Installation

1. **Clone Repository**:
   ```bash
   git clone https://github.com/TonyMa1/python-arbitrage-monitor.git
   cd python-arbitrage-monitor
   ```

2. **Create Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # macOS/Linux
   venv\Scripts\activate     # Windows
   ```

3. **Install Dependencies**:
   ```bash
   # Using uv (faster installation)
   pip install uv
   uv pip install -r requirements.txt
   
   # Or standard pip
   pip install -r requirements.txt
   ```

## Usage

### Basic Command

```bash
python main.py --market-a <exchange1.type> --market-b <exchange2.type> [options]
```

### Parameters

| Parameter | Description | Default | Example |
|-----------|-------------|---------|---------|
| `--market-a` | First market (format: exchange.type[.subtype]) | `binance.spot` | `binance.future.linear` |
| `--market-b` | Second market (format: exchange.type[.subtype]) | `okx.swap.linear` | `bybit.spot` |
| `--quote-currency` | Base quote currency for filtering pairs | `USDT` | `BTC` |
| `--symbols` | Specific pairs to monitor (comma-separated) | None | `BTC-USDT,ETH-USDT` |
| `--topn` | Number of top spreads to display | `20` | `10` |

### Example Scenarios

```bash
# Compare stablecoin markets
python main.py --market-a binance.spot --market-b okx.spot --symbols USDC-USDT,BUSD-USDT,DAI-USDT

# Monitor BTC-quoted pairs
python main.py --market-a binance.spot --market-b okx.spot --quote-currency BTC --topn 15

# Compare futures markets
python main.py --market-a binance.future.linear --market-b okx.future.linear
```

## Configuration

### Environment Variables

Override default configuration via environment variables:

```bash
# Example: Set background thread pool size
export ARBITRAGE_MONITOR_THREAD_POOL_SIZE=8

# Example: Set update interval (milliseconds)
export ARBITRAGE_MONITOR_UPDATE_INTERVAL_MS=1000
```

### Proxy Settings

The application supports HTTP/HTTPS proxies for connections:

```bash
# Set proxy environment variables
export http_proxy="http://proxy.example.com:8080"
export https_proxy="http://proxy.example.com:8080"
```

## Technical Details

- **Data Flow**: Real-time data → Threaded spread calculation → Sorted display
- **Market Definitions**: Supports format `exchange.type[.subtype]` where:
  - `exchange`: Exchange name (binance, okx, etc.)
  - `type`: Market type (spot, future, swap)
  - `subtype`: Additional specification (linear, inverse)
- **Error Handling**: Implements exponential backoff for network issues
- **Thread Management**: Calculation-heavy tasks run in separate threads
- **UI Architecture**: Built on Textual with responsive data tables

## Origin

Developed for MFIN7037 Quantitative Trading course in the Master of Financial Technology program at The University of Hong Kong (HKU).

## Requirements

- Python 3.11+ (recommended)
- Dependencies listed in `requirements.txt`

## License

[MIT License](LICENSE)

