# region imports
from AlgorithmImports import *
# endregion

class CompleteFVGTradingSystem(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2023, 6, 1)
        self.set_end_date(2023, 6, 30)
        self.set_cash(1000000)
        
        # Add NQ futures
        self.nq = self.add_future(Futures.Indices.NASDAQ_100_E_MINI, Resolution.MINUTE)
        self.nq.set_filter(timedelta(0), timedelta(182))
        
        # Track everything
        self.current_contract = None
        self.daily_trade_taken = False
        self.position_size = 1
        
        # 7 SMA for daily bias
        self.sma_period = 7
        self.daily_sma = None
        self.daily_bias = None
        
        # Debug counters
        self.days_with_data = 0
        self.fvg_patterns_found = 0
        self.trades_attempted = 0
        self.bias_mismatches = 0
        self.time_window_checks = 0
        
        self.set_warmup(self.sma_period, Resolution.DAILY)

    def on_data(self, slice):
        if self.is_warming_up:
            return

        # Track daily activity and reset trade flag
        current_day = slice.time.date()
        if not hasattr(self, 'last_day') or self.last_day != current_day:
            self.days_with_data += 1
            self.last_day = current_day
            self.daily_trade_taken = False  # Reset for new day

        # Get current contract and set up SMA
        if not self.current_contract:
            for chain in slice.future_chains:
                if chain.key == self.nq.symbol:
                    contracts = [x for x in chain.value if x.expiry > self.time]
                    if contracts:
                        self.current_contract = sorted(contracts, key=lambda x: x.expiry)[0].symbol
                        # Set up 7-day SMA for this contract
                        self.daily_sma = self.sma(self.current_contract, self.sma_period, Resolution.DAILY)
                        break
        
        if not self.current_contract or self.current_contract not in slice.bars:
            return

        # Skip if already traded today
        if self.daily_trade_taken:
            return

        # Calculate daily bias using 7 SMA
        if not self.daily_sma or not self.daily_sma.is_ready:
            return

        current_price = slice.bars[self.current_contract].close
        sma_value = self.daily_sma.current.value
        self.daily_bias = 'Bullish' if current_price > sma_value else 'Bearish'

        # REQUIREMENT 1: Only after 9:30 AM
        current_time = slice.time
        market_open = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
        
        if current_time < market_open:
            return  # Wait until after 9:30 AM
            
        self.time_window_checks += 1

        # REQUIREMENT 2: Find FIRST FVG after 9:30 AM today
        self.find_first_fvg_after_930(slice, current_time, market_open)

    def find_first_fvg_after_930(self, slice, current_time, market_open):
        # Calculate how much data we need from 9:30 AM to now
        minutes_since_930 = int((current_time - market_open).total_seconds() / 60)
        lookback_minutes = max(10, minutes_since_930 + 5)  # At least 10 minutes of data
        
        # Get historical data from around market open
        history = self.history([self.current_contract], lookback_minutes, Resolution.MINUTE)
        if history.empty or len(history) < 3:
            return

        data = history.reset_index(level=0, drop=True)
        
        # REQUIREMENT 3: 3 candlestick pattern logic - look for FIRST FVG (any type)
        for i in range(len(data) - 2):
            c1 = data.iloc[i]      # First candle
            c2 = data.iloc[i + 1]  # Second candle (the gap candle)
            c3 = data.iloc[i + 2]  # Third candle
            
            c1_high = float(c1['high'])
            c1_low = float(c1['low'])
            c3_high = float(c3['high'])
            c3_low = float(c3['low'])
            
            # Check for ANY FVG pattern (bullish OR bearish)
            is_bullish_fvg = c1_high < c3_low  # Gap up
            is_bearish_fvg = c1_low > c3_high  # Gap down
            
            if is_bullish_fvg or is_bearish_fvg:
                self.fvg_patterns_found += 1
                
                # Found FIRST FVG - now use DAILY BIAS for trade direction
                current_bar = slice.bars[self.current_contract]
                entry_price = float(current_bar.close)
                
                if self.daily_bias == 'Bullish':
                    # BULLISH BIAS: Look for price to close above FVG area for long entry
                    if is_bullish_fvg:
                        # Bullish FVG: Enter if price closes above c1.low
                        fvg_trigger_level = c1_low
                        stop_loss = c3_low  # Stop below the gap
                    else:
                        # Bearish FVG: Enter if price closes above c1.low  
                        fvg_trigger_level = c1_low
                        stop_loss = c3_high  # Stop below the gap area
                    
                    risk = entry_price - stop_loss
                    take_profit = entry_price + risk  # 1:1 RR
                    
                    # Enter long if price closes above FVG trigger level
                    if risk > 0 and entry_price > fvg_trigger_level and self.current_contract:
                        self.trades_attempted += 1
                        self.market_order(self.current_contract, self.position_size)
                        self.stop_market_order(self.current_contract, -self.position_size, stop_loss)
                        self.limit_order(self.current_contract, -self.position_size, take_profit)
                        self.daily_trade_taken = True
                        return  # Found first FVG and entered, stop looking
                
                elif self.daily_bias == 'Bearish':
                    # BEARISH BIAS: Look for price to close below FVG area for short entry
                    if is_bearish_fvg:
                        # Bearish FVG: Enter if price closes below c3.high
                        fvg_trigger_level = c3_high
                        stop_loss = c1_high  # Stop above the gap
                    else:
                        # Bullish FVG: Enter if price closes below c3.high
                        fvg_trigger_level = c3_high  
                        stop_loss = c1_low   # Stop above the gap area
                    
                    risk = stop_loss - entry_price
                    take_profit = entry_price - risk  # 1:1 RR
                    
                    # Enter short if price closes below FVG trigger level
                    if risk > 0 and entry_price < fvg_trigger_level and self.current_contract:
                        self.trades_attempted += 1
                        self.market_order(self.current_contract, -self.position_size)
                        self.stop_market_order(self.current_contract, self.position_size, stop_loss)
                        self.limit_order(self.current_contract, self.position_size, take_profit)
                        self.daily_trade_taken = True
                        return  # Found first FVG and entered, stop looking
                
                else:
                    self.bias_mismatches += 1  # Found FVG but no clear bias

    def on_end_of_day(self, symbol):
        if not self.is_warming_up and self.current_contract:
            if self.portfolio[self.current_contract].invested:
                self.liquidate(self.current_contract)
            self.transactions.cancel_open_orders(self.current_contract)

    def on_end_of_algorithm(self):
        # Enhanced debug info
        self.log(f"FINAL STATS:")
        self.log(f"Days with data: {self.days_with_data}")
        self.log(f"Time window checks (after 9:30): {self.time_window_checks}")
        self.log(f"FVG patterns found: {self.fvg_patterns_found}")
        self.log(f"Bias mismatches: {self.bias_mismatches}")
        self.log(f"Trades attempted: {self.trades_attempted}")

    def on_securities_changed(self, changes):
        for security in changes.added_securities:
            if security.symbol.security_type == SecurityType.FUTURE:
                security.set_leverage(2.0)

    def on_order_event(self, order_event):
        if order_event.status == OrderStatus.FILLED:
            pass
