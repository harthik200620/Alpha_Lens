import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'news_cache.db')

def connect_news_db():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn

def run_performance_check():
    """
    Analyzes the 'stock_impact' table to report prediction accuracy.
    Now includes Expired status tracking and excludes expired/active from win rate calculation.
    """
    print("\n" + "=" * 60)
    print("  ALPHA LENS v2.0: STRATEGY PERFORMANCE REPORT")
    print("=" * 60)

    try:
        conn = connect_news_db()
        c = conn.cursor()

        # Get Total News Articles
        c.execute("SELECT COUNT(*) FROM news")
        total_news = c.fetchone()[0]

        # Get Counts by Status
        c.execute("""
            SELECT status, COUNT(*) as count 
            FROM stock_impact 
            GROUP BY status
        """)
        status_counts = {row['status']: row['count'] for row in c.fetchall()}

        hits = status_counts.get('Predicted Target Hit', 0)
        misses = status_counts.get('Reacted Against Prediction', 0) + status_counts.get('Stop Loss Hit', 0)
        active = status_counts.get('Active View', 0)
        expired = status_counts.get('Expired', 0)
        total_calls = hits + misses + active + expired

        # Color coding in terminal
        GREEN = "\033[92m"
        RED = "\033[91m"
        CYAN = "\033[96m"
        YELLOW = "\033[93m"
        RESET = "\033[0m"
        BOLD = "\033[1m"

        # Win Rate calculation (only on RESOLVED trades — excluding active and expired)
        resolved_trades = hits + misses
        if resolved_trades > 0:
            win_rate = round((hits / resolved_trades) * 100, 2)
            
            if win_rate >= 70:
                rating = f"{GREEN} ELITE{RESET}"
            elif win_rate >= 55:
                rating = f"{CYAN} SOLID{RESET}"
            elif win_rate >= 45:
                rating = f"{YELLOW} MODERATE{RESET}"
            else:
                rating = f"{RED} NEEDS IMPROVEMENT{RESET}"
                
            win_display = f"{win_rate}%  ({rating})"
        else:
            win_display = f"{YELLOW}N/A (No resolved trades yet){RESET}"


        # Get average confidence of winning vs losing trades
        c.execute("SELECT AVG(confidence_score) FROM stock_impact WHERE status = 'Predicted Target Hit'")
        avg_win_confidence = c.fetchone()[0] or 0

        c.execute("SELECT AVG(confidence_score) FROM stock_impact WHERE status IN ('Reacted Against Prediction', 'Stop Loss Hit')")
        avg_loss_confidence = c.fetchone()[0] or 0

        # Print the Report
        print(f"Total News Articles Analyzed:       {total_news}")
        print(f"Total Stock Calls Triggered:        {total_calls}")
        print("-" * 60)

        print(f"{GREEN} TARGET HIT (Wins):               {hits}{RESET}")
        print(f"{RED} REACTED AGAINST (Losses):         {misses}{RESET}")
        print(f"{YELLOW}⏰ EXPIRED (No move in 3 days):      {expired}{RESET}")
        print(f"{CYAN}⏳ STILL RUNNING (Active):           {active}{RESET}")
        print("-" * 60)

        # Confidence analysis
        if avg_win_confidence > 0 or avg_loss_confidence > 0:
            print(f"Avg Confidence on Wins:              {round(avg_win_confidence, 1)}")
            print(f"Avg Confidence on Losses:            {round(avg_loss_confidence, 1)}")
            print("-" * 60)

        print(f"{BOLD} AI STRATEGY WIN RATE:             {win_display}{RESET}")
        print(f"   (Based on {resolved_trades} resolved trades)")
        print(f"   (Excluding {expired} expired + {active} still active)")
        print("=" * 60 + "\n")

        conn.close()
    except Exception as e:
        print(f" Error during performance check: {e}")

if __name__ == "__main__":
    run_performance_check()
