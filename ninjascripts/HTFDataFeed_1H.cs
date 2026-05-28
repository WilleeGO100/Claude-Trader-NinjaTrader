#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
#endregion

// Apply this strategy to a 1-HOUR NQ chart.
// Writes two files:
//   HistoricalData_1H.csv         — completed 1H bars (on bar close)
//   HistoricalData_1H_current.csv — current in-progress bar OHLC (throttled to every 10s)

namespace NinjaTrader.NinjaScript.Strategies
{
    public class HTFDataFeed_1H : Strategy
    {
        private string   filePath;
        private string   currentBarPath;
        private bool     isFileInitialized = false;
        private bool     isFirstBar        = true;
        private DateTime lastCurrentWrite  = DateTime.MinValue;
        private const int WriteIntervalSec = 10;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description         = "Exports 1H OHLC bars + throttled intrabar update for Claude Trader";
                Name                = "HTFDataFeed_1H";
                Calculate           = Calculate.OnPriceChange;
                BarsRequiredToTrade = 5;
                EntriesPerDirection = 1;

                OutputFilePath = @"C:\Users\jwmar\Claude-Trader-NinjaTrader\data\HistoricalData_1H.csv";
            }
            else if (State == State.DataLoaded)
            {
                filePath       = OutputFilePath;
                currentBarPath = System.IO.Path.Combine(
                    System.IO.Path.GetDirectoryName(OutputFilePath),
                    "HistoricalData_1H_current.csv"
                );
                Print($"HTFDataFeed_1H ready — historical: {filePath}  current: {currentBarPath}");
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BarsRequiredToTrade)
                return;

            if (!isFileInitialized)
            {
                InitFile();
                isFileInitialized = true;
            }

            if (IsFirstTickOfBar && !isFirstBar)
                AppendClosedBar();

            // Only write current bar file every N seconds to avoid freezing
            if ((DateTime.Now - lastCurrentWrite).TotalSeconds >= WriteIntervalSec)
            {
                WriteCurrentBar();
                lastCurrentWrite = DateTime.Now;
            }

            isFirstBar = false;
        }

        private void InitFile()
        {
            try
            {
                using (StreamWriter sw = new StreamWriter(filePath, false))
                    sw.WriteLine("DateTime,Open,High,Low,Close");
            }
            catch (Exception ex)
            {
                Print($"HTFDataFeed_1H error initializing file: {ex.Message}");
            }
        }

        private void AppendClosedBar()
        {
            try
            {
                using (StreamWriter sw = new StreamWriter(filePath, true))
                    sw.WriteLine($"{Time[1]:MM/dd/yyyy HH:mm:ss},{Open[1]:F2},{High[1]:F2},{Low[1]:F2},{Close[1]:F2}");
            }
            catch (Exception ex)
            {
                Print($"HTFDataFeed_1H error writing closed bar: {ex.Message}");
            }
        }

        private void WriteCurrentBar()
        {
            try
            {
                using (StreamWriter sw = new StreamWriter(currentBarPath, false))
                {
                    sw.WriteLine("DateTime,Open,High,Low,Close,Intrabar");
                    sw.WriteLine($"{Time[0]:MM/dd/yyyy HH:mm:ss},{Open[0]:F2},{High[0]:F2},{Low[0]:F2},{Close[0]:F2},true");
                }
            }
            catch (Exception ex)
            {
                Print($"HTFDataFeed_1H error writing current bar: {ex.Message}");
            }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Output File Path", Order = 1, GroupName = "HTFDataFeed_1H")]
        public string OutputFilePath { get; set; }
        #endregion
    }
}
