#region Using declarations
using System;
using System.IO;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
#endregion

// Apply this strategy to a 4-HOUR NQ chart.
// It writes completed 4H OHLC bars to data/HistoricalData_4H.csv
// for the Python HTF analyzer to read.

namespace NinjaTrader.NinjaScript.Strategies
{
    public class HTFDataFeed : Strategy
    {
        private string filePath;
        private bool isFileInitialized = false;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description         = "Exports 4H OHLC bars for Claude Trader HTF context";
                Name                = "HTFDataFeed";
                Calculate           = Calculate.OnBarClose;
                BarsRequiredToTrade = 5;
                EntriesPerDirection = 1;

                OutputFilePath = @"C:\Users\jwmar\Claude-Trader-NinjaTrader\data\HistoricalData_4H.csv";
            }
            else if (State == State.DataLoaded)
            {
                filePath = OutputFilePath;
                Print($"HTFDataFeed ready — writing to {filePath}");
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
                Print($"HTFDataFeed error initializing file: {ex.Message}");
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
                Print($"HTFDataFeed error writing bar: {ex.Message}");
            }
        }

        #region Properties
        [NinjaTrader.NinjaScript.NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Output File Path", Order = 1, GroupName = "HTFDataFeed")]
        public string OutputFilePath { get; set; }
        #endregion
    }
}
