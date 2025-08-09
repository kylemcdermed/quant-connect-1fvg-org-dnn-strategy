# region imports
from AlgorithmImports import *
# endregion

class CompleteFVGTradingSystem(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2023, 6, 1)  # Earlier historical data
        self.set_end_date(2023, 6, 30)   # Fully in-sample
        self.set_cash(10000)
        
        # Add NQ=F (E-mini Nasdaq-100) futures
        self.nq_future = self.add_future(Futures.Indices.NASDAQ_100_E_MINI, Resolution.MINUTE)
        self.nq_future.set_filter(timedelta(0), timedelta(90))  # Filter contracts expiring within 90 days
        
        self.sma_period = 7
        self.win_prob = 0.5
        self.account_balance = 10000
        self.first_fvg = None
        self.entry_signal = None
        self.orders = []
        self.trade_taken = False
        self.trade_result = None
        
        # Warm up with daily SMA
        self.daily_sma = self.sma(self.nq_future.Symbol, self.sma_period, Resolution.DAILY)
        self.set_warmup(self.sma_period, Resolution.DAILY)
        self.debug(f"Initialized with Start: {self.start_date}, End: {self.end_date}, Symbol: {self.nq_future.Symbol}")

    def on_data(self, slice):
        if not self.is_warming_up:
            self.debug(f"Processing data at {slice.Time} for {self.nq_future.Symbol}")
            # Calculate daily bias using SMA
            if self.nq_future.Symbol in slice.Bars and self.daily_sma.is_ready:
                latest_bar = slice.Bars[self.nq_future.Symbol]
                sma_value = self.daily_sma.current.value
                self.daily_bias = 'Bullish' if latest_bar.Close > sma_value else 'Bearish'
                self.debug(f"Daily Bias: {self.daily_bias} (Close: ${latest_bar.Close:.2f} vs 7-SMA: ${sma_value:.2f})")
            else:
                self.debug("No bars or SMA not ready")
                return

            # Detect FVG and manage trade
            self.detect_fvg_and_entry(slice)

    def calculate_kelly_fraction(self, reward_to_risk):
        p = self.win_prob
        q = 1 - p
        b = reward_to_risk
        f = (p * (b + 1) - q) / b if b > 0 else 0
        return max(0, min(0.5, f))

    def detect_fvg_and_entry(self, slice):
        if self.trade_taken or self.nq_future.Symbol not in slice.Bars:
            self.debug("Trade already taken or no 1m data available, no further trades allowed.")
            return

        # Use current time for 9:30-11:00 AM EST window (adjust for exchange time zone)
        current_time = slice.Time
        time_start = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
        time_end = current_time.replace(hour=11, minute=0, second=0, microsecond=0)
        if not (time_start <= current_time <= time_end):
            return

        current_bar = slice.Bars[self.nq_future.Symbol]
        data = self.history(self.nq_future.Symbol, 240, Resolution.MINUTE)
        if data.empty:
            self.debug(f"History data is empty for {self.nq_future.Symbol} at {slice.Time}")
            return

        self.debug(f"Processing {len(data)} bars from {data.index[0]} to {data.index[-1]}")
        for i in range(len(data) - 2):
            c1 = data.iloc[i]
            c2 = data.iloc[i + 1]
            c3 = data.iloc[i + 2]
            if c1['low'] > c3['high']:
                fvg_open, fvg_close = c3['high'], c1['low']
                self.first_fvg = {'type': 'First FVG', 'fvg_open': fvg_open, 'fvg_close': fvg_close, 'c1_low': c1['low'],
                                'c1_high': c1['high'], 'c3_low': c3['low'], 'c3_high': c3['high'], 'candle_indices': [i, i+1, i+2]}
            elif c1['high'] < c3['low']:
                fvg_open, fvg_close = c1['high'], c3['low']
                self.first_fvg = {'type': 'First FVG', 'fvg_open': fvg_open, 'fvg_close': fvg_close, 'c1_low': c1['low'],
                                'c1_high': c1['high'], 'c3_low': c3['low'], 'c3_high': c3['high'], 'candle_indices': [i, i+1, i+2]}
            else:
                continue

            if self.first_fvg:
                self.debug("\nFVG DETECTED:")
                self.debug(f"Type: {self.first_fvg['type']}")
                self.debug(f"FVG Zone: ${self.first_fvg['fvg_open']:.2f} - ${self.first_fvg['fvg_close']:.2f}")
                
                if self.daily_bias == 'Bullish':
                    entry_price = self.first_fvg['c1_low']
                    stop_loss = self.first_fvg['c3_low']
                    risk = entry_price - stop_loss
                    tp_1 = entry_price + risk
                    tp_2 = entry_price + 2 * risk
                    tp_3 = entry_price + 3 * risk
                    kelly_fractions = [self.calculate_kelly_fraction(r) for r in [1, 2, 3]]
                    self.orders = [
                        {'rr': 1, 'entry': entry_price, 'stop': stop_loss, 'tp': tp_1, 'kelly': kelly_fractions[0]},
                        {'rr': 2, 'entry': entry_price, 'stop': stop_loss, 'tp': tp_2, 'kelly': kelly_fractions[1]},
                        {'rr': 3, 'entry': entry_price, 'stop': stop_loss, 'tp': tp_3, 'kelly': kelly_fractions[2]}
                    ]
                    if time_start <= current_time <= time_end and not self.trade_taken:
                        if current_bar.Close > entry_price:
                            self.entry_signal = {'candle_index': i, 'entry_candle': current_bar, 'entry_time': current_time}
                            self.trade_taken = True
                            self.debug(f"BULLISH ENTRY TRIGGERED at {current_time.strftime('%H:%M')} (Close: ${current_bar.Close:.2f} > ${entry_price:.2f})")
                            active_orders = self.orders.copy()
                            for order in active_orders:
                                self.debug(f"   Order {order['rr']}:1 RR - Entry: ${order['entry']:.2f}, Stop: ${order['stop']:.2f}, TP: ${order['tp']:.2f}, Kelly: {order['kelly']:.2%}")
                                if current_bar.Close >= order['tp']:
                                    if order['rr'] == 1:
                                        for o in active_orders:
                                            o['stop'] = o['entry']
                                        self.debug(f"   1:1 RR hit, stopped all to entry: ${entry_price:.2f}")
                                    elif order['rr'] == 2:
                                        for o in [o for o in active_orders if o['rr'] > 1]:
                                            o['stop'] = tp_1
                                        self.debug(f"   1:2 RR hit, stopped remaining to 1:1 TP: ${tp_1:.2f}")
                                    elif order['rr'] == 3:
                                        active_orders = []
                                        self.debug(f"   1:3 RR hit, all orders booked")
                            self.orders = active_orders
                            if current_bar.Close <= stop_loss:
                                self.trade_result = "Stop Loss hit -50"
                            elif not self.orders and any(o['tp'] == tp_1 for o in active_orders if o['rr'] == 1):
                                self.trade_result = "Final order stopped in profit at 1:1 RR +0, Total Profit +50"
                            elif tp_3 <= current_bar.Close < tp_1:
                                self.trade_result = "Take Profit 1:3 hit +0"
                            elif tp_2 <= current_bar.Close < tp_3:
                                self.trade_result = "Take Profit 1:2 hit +0"
                            elif tp_1 <= current_bar.Close < tp_2:
                                self.trade_result = "Take Profit 1:1 hit +0"
                            if not self.orders:
                                break
                elif self.daily_bias == 'Bearish':
                    entry_price = self.first_fvg['c3_high']
                    stop_loss = self.first_fvg['c1_high']
                    risk = stop_loss - entry_price
                    tp_1 = entry_price - risk
                    tp_2 = entry_price - 2 * risk
                    tp_3 = entry_price - 3 * risk
                    kelly_fractions = [self.calculate_kelly_fraction(r) for r in [1, 2, 3]]
                    self.orders = [
                        {'rr': 1, 'entry': entry_price, 'stop': stop_loss, 'tp': tp_1, 'kelly': kelly_fractions[0]},
                        {'rr': 2, 'entry': entry_price, 'stop': stop_loss, 'tp': tp_2, 'kelly': kelly_fractions[1]},
                        {'rr': 3, 'entry': entry_price, 'stop': stop_loss, 'tp': tp_3, 'kelly': kelly_fractions[2]}
                    ]
                    if time_start <= current_time <= time_end and not self.trade_taken:
                        if current_bar.Close < entry_price:
                            self.entry_signal = {'candle_index': i, 'entry_candle': current_bar, 'entry_time': current_time}
                            self.trade_taken = True
                            self.debug(f"BEARISH ENTRY TRIGGERED at {current_time.strftime('%H:%M')} (Close: ${current_bar.Close:.2f} < ${entry_price:.2f})")
                            active_orders = self.orders.copy()
                            for order in active_orders:
                                self.debug(f"   Order {order['rr']}:1 RR - Entry: ${order['entry']:.2f}, Stop: ${order['stop']:.2f}, TP: ${order['tp']:.2f}, Kelly: {order['kelly']:.2%}")
                                if current_bar.Close <= order['tp']:
                                    if order['rr'] == 1:
                                        for o in active_orders:
                                            o['stop'] = o['entry']
                                        self.debug(f"   1:1 RR hit, stopped all to entry: ${entry_price:.2f}")
                                    elif order['rr'] == 2:
                                        for o in [o for o in active_orders if o['rr'] > 1]:
                                            o['stop'] = tp_1
                                        self.debug(f"   1:2 RR hit, stopped remaining to 1:1 TP: ${tp_1:.2f}")
                                    elif order['rr'] == 3:
                                        active_orders = []
                                        self.debug(f"   1:3 RR hit, all orders booked")
                            self.orders = active_orders
                            if current_bar.Close >= stop_loss:
                                self.trade_result = "Stop Loss hit -50"
                            elif not self.orders and any(o['tp'] == tp_1 for o in active_orders if o['rr'] == 1):
                                self.trade_result = "Final order stopped in profit at 1:1 RR +0, Total Profit +50"
                            elif tp_1 > current_bar.Close >= tp_3:
                                self.trade_result = "Take Profit 1:3 hit +0"
                            elif tp_2 > current_bar.Close >= tp_3:
                                self.trade_result = "Take Profit 1:2 hit +0"
                            elif tp_1 > current_bar.Close >= tp_2:
                                self.trade_result = "Take Profit 1:1 hit +0"
                            if not self.orders:
                                break
                break  # Exit after detecting the first FVG

    def on_end_of_day(self, symbol):
        if self.trade_result and symbol == self.nq_future.Symbol:
            self.debug(f"\nTRADE RESULT: {self.trade_result}")
        self.trade_taken = False
        self.orders = []
        self.entry_signal = None
        self.first_fvg = None

    def on_securities_changed(self, changes):
        for security in changes.AddedSecurities:
            security.set_leverage(2.0)  # Adjust leverage for futures
            self.debug(f"Added security: {security.Symbol}")

if __name__ == "__main__":
    print("Complete FVG Trading System - CANDLE IDENTIFICATION")
    print("=" * 60)
    print("CANDLE IDENTIFICATION:")
    print("   CANDLE #1 = Purple = First FVG candle")
    print("   CANDLE #2 = Orange = Second FVG candle")
    print("   CANDLE #3 = Cyan = Third FVG candle")
    print("=" * 60)
