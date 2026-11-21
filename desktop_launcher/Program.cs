using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;

[assembly: AssemblyTitle("Tone Lab")]
[assembly: AssemblyDescription("Tone Lab for Quad Cortex")]
[assembly: AssemblyProduct("Tone Lab")]

internal static class Program
{
    private const string AppId = "ToneLab.QuadCortex.Desktop";

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int MessageBoxW(IntPtr owner, string message, string title, uint flags);

    // Arguments are passed on to start.pyw: "--new" opens the rewritten desk, "--old" the earlier one. Without
    // either, start.pyw decides (TONE_LAB_DESK, then launcher.json in the data folder, then the rewritten desk).
    // start.pyw takes this same AppId for its own process, which is the one that owns the window.
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            SetCurrentProcessExplicitAppUserModelID(AppId);
            string folder = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(folder, ".venv", "Scripts", "pythonw.exe");
            string startup = Path.Combine(folder, "desktop_launcher", "start.pyw");

            if (!File.Exists(python))
                return Fail("Tone Lab's local Python environment is missing. Open the project README for setup.");
            if (!File.Exists(startup))
                return Fail("The Tone Lab startup file is missing. Open Tone Lab from its project folder.");

            string arguments = "\"" + startup + "\"";
            foreach (string argument in args)
                arguments += " " + Quote(argument);
            var start = new ProcessStartInfo(python, arguments)
            {
                WorkingDirectory = folder,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using (Process process = Process.Start(start))
            {
                if (process == null)
                    return Fail("Windows did not start Tone Lab's local Python environment.");
                if (process.WaitForExit(3000) && process.ExitCode != 0 && process.ExitCode != 2)
                    return Fail("Tone Lab's local Python environment exited during startup (code "
                                + process.ExitCode + "). Open the project README for setup.");
            }
            return 0;
        }
        catch (Exception error)
        {
            return Fail("Windows couldn't start Tone Lab." + Environment.NewLine + error.Message);
        }
    }

    private static string Quote(string argument)
    {
        // Simple flags such as --new pass as they are; anything else is quoted for the Windows command line.
        if (argument.Length > 0 && argument.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            return argument;
        var quoted = new System.Text.StringBuilder("\"");
        int slashes = 0;
        foreach (char c in argument)
        {
            if (c == '\\') { slashes++; continue; }
            // Backslashes before a quote are doubled and the quote escaped; elsewhere they stand as they are.
            quoted.Append('\\', c == '"' ? slashes * 2 + 1 : slashes).Append(c);
            slashes = 0;
        }
        return quoted.Append('\\', slashes * 2).Append('"').ToString();
    }

    private static int Fail(string message)
    {
        const uint visibleError = 0x10 | 0x10000 | 0x40000; // MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
        MessageBoxW(IntPtr.Zero, message, "Tone Lab startup", visibleError);
        return 1;
    }
}
