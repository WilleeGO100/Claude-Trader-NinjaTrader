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
// Writes completed 1H OHLC bars to HistoricalData_1H.csv on bar close.

namespace NinjaTrader.NinjaScript.Strategies
{
    public class HTFDataFeed_1H : Strategy
    {
        private string filePath;
        private bool isFileInitialized = false;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description         = "Exports 1H OHLC bars for Claude Trader intraday structure context";
                Name                = "HTFDataFeed_1H";
                Calculate           = Calculate.OnBarClose;
                BarsRequiredToTrade = 5;
                EntriesPerDirection = 1;

                OutputFilePath = @"C:\Users\jwmar\Claude-Trader-NinjaTrader\data\HistoricalData_1H.csv";
            }
            else if (State == State.DataLoaded)
            {
                filePath = OutputFilePath;
                Print($"HTFDataFeed_1H ready — writing to {filePath}");
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

            AppendBar();
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

        private void AppendBar()
        {
            try
            {
                using (StreamWriter sw = new StreamWriter(filePath, true))
                    sw.WriteLine($"{Time[0]:MM/dd/yyyy HH:mm:ss},{Open[0]:F2},{High[0]:F2},{Low[0]:F2},{Close[0]:F2}");
            }
            catch (Exception ex)
            {
                Print($"HTFDataFeed_1H error writing bar: {ex.Message}");
            }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Output File Path", Order = 1, GroupName = "HTFDataFeed_1H")]
        public string OutputFilePath { get; set; }
        #endregion
    }
}
