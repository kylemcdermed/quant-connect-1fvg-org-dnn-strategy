# region imports
from AlgorithmImports import *
# endregion

class DebugFVGTradingSystem(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2023, 6, 1)
        self.set_end_date(2023, 6, 30)
        self.set_cash(1000000)
        
        # Add NQ futures
        self.nq = self.add_future(Futures.Indices.NASDAQ_100_E_MINI, Resolution.MINUTE)
        self.nq.set_filter(timedelta(0), timedelta(182))
        
        # Track everything for debugging
        self.current_contract = None
        self.trade_taken = False
        self.position_size = 1
        
        # DEBUG COUNTERS
        self.days_with_data = 0
        self.total_on_data_calls = 0
        self.contract_changes = 0
        self.fvg_patterns_found = 0
        self.trades_attempted = 0
        self.end_of_day_calls = 0
        
        self.set_warmup(1)

    def on_data(self, slice):
        self.total_on_data_calls += 1
        
        if self.is_warming_up:
            return

        # Check if we have any futures data at all
        has_futures_data = len(slice.future_chains) > 0
        has_bar_data = len(slice.bars) > 0
        
        # Track daily activity
        current_day = slice.time.date()
        if not hasattr(self, 'last_day') or self.last_day != current_day:
            self.days_with_data += 1
            self.last_day = current_day
            # Reset trade_taken each new day for testing
            self.trade_taken = False

        # Get current contract
        if not self.current_contract:
            for chain in slice.future_chains:
                if chain.key == self.nq.symbol:
                    contracts = [x for x in chain.value if x.expiry > self.time]
                    if contracts:
                        old_contract = self.current_contract
                        self.current_contract = sorted(contracts, key=lambda x: x.expiry)[0].symbol
                        if old_contract != self.current_contract:
                            self.contract_changes += 1
                        break
        
        if not self.current_contract or self.current_contract not in slice.bars:
            return

        # Look for FVG only if we haven't traded today
        if self.trade_taken:
            return

        # Get historical data
        history = self.history([self.current_contract], 10, Resolution.MINUTE)
        if history.empty or len(history) < 3:
            return

        data = history.reset_index(level=0, drop=True)
        
        # Look for FVG pattern
        for i in range(len(data) - 2):
            c1 = data.iloc[i]
            c2 = data.iloc[i + 1] 
            c3 = data.iloc[i + 2]
            
            c1_high = float(c1['high'])
            c1_low = float(c1['low'])
            c3_high = float(c3['high'])
            c3_low = float(c3['low'])
            
            # Check for any FVG
            is_bullish_fvg = c1_high < c3_low
            is_bearish_fvg = c1_low > c3_high
            
            if is_bullish_fvg or is_bearish_fvg:
                self.fvg_patterns_found += 1
                
                # Try to enter trade
                current_bar = slice.bars[self.current_contract]
                entry_price = float(current_bar.close)
                
                if is_bullish_fvg:
                    stop_loss = c1_high
                    risk = entry_price - stop_loss
                    take_profit = entry_price + risk
                    
                    if risk > 0:
                        self.trades_attempted += 1
                        self.market_order(self.current_contract, self.position_size)
                        self.stop_market_order(self.current_contract, -self.position_size, stop_loss)
                        self.limit_order(self.current_contract, -self.position_size, take_profit)
                        self.trade_taken = True
                        return
                
                elif is_bearish_fvg:
                    stop_loss = c1_low
                    risk = stop_loss - entry_price
                    take_profit = entry_price - risk
                    
                    if risk > 0:
                        self.trades_attempted += 1
                        self.market_order(self.current_contract, -self.position_size)
                        self.stop_market_order(self.current_contract, self.position_size, stop_loss)
                        self.limit_order(self.current_contract, self.position_size, take_profit)
                        self.trade_taken = True
                        return

    def on_end_of_day(self, symbol):
        self.end_of_day_calls += 1
        
        if not self.is_warming_up and self.current_contract:
            if self.portfolio[self.current_contract].invested:
                self.liquidate(self.current_contract)
            self.transactions.cancel_open_orders(self.current_contract)
        
        # Reset for next day - THIS IS CRITICAL
        self.trade_taken = False

    def on_end_of_algorithm(self):
        # Print debug info
        self.log(f"FINAL STATS:")
        self.log(f"Days with data: {self.days_with_data}")
        self.log(f"Total on_data calls: {self.total_on_data_calls}")
        self.log(f"Contract changes: {self.contract_changes}")
        self.log(f"FVG patterns found: {self.fvg_patterns_found}")
        self.log(f"Trades attempted: {self.trades_attempted}")
        self.log(f"End of day calls: {self.end_of_day_calls}")

    def on_securities_changed(self, changes):
        for security in changes.added_securities:
            if security.symbol.security_type == SecurityType.FUTURE:
                security.set_leverage(1)

    def on_order_event(self, order_event):
        if order_event.status == OrderStatus.FILLED:
            pass
