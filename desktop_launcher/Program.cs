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

    [STAThread]
    private static int Main()
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

            var start = new ProcessStartInfo(python, "\"" + startup + "\"")
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

    private static int Fail(string message)
    {
        const uint visibleError = 0x10 | 0x10000 | 0x40000; // MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
        MessageBoxW(IntPtr.Zero, message, "Tone Lab startup", visibleError);
        return 1;
    }
}
