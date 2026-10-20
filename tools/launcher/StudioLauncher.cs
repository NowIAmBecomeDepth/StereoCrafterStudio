// StereoCrafter Studio.exe -- starts Studio, with the Studio icon (user,
// 2026-09-28: a .bat cannot carry an icon). Does what "StereoCrafter
// Studio.bat" does, without its console flash:
//
//   * runs the venv's pythonw.exe -u -m studio.app from the folder this
//     exe sits in (the repo root), with no window of its own;
//   * writes Studio's output to studio_last_run.log, as the .bat does;
//   * stays in the background, invisible, until Studio closes -- and if Studio
//     exits with an error, offers to open that log.
//
// Built by tools\launcher\build_launcher.bat with the C# compiler that ships
// with Windows (.NET Framework 4): nothing to install. C# 5, on purpose.

using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

static class StudioLauncher
{
    const string Title = "StereoCrafter Studio";

    [STAThread]
    static int Main()
    {
        string root = Path.GetDirectoryName(Application.ExecutablePath);
        string pythonw = Path.Combine(root, @".venv\Scripts\pythonw.exe");
        string log = Path.Combine(root, "studio_last_run.log");

        if (!File.Exists(pythonw))
        {
            MessageBox.Show("The virtual environment was not found.\n\nExpected:\n" + pythonw +
                            "\n\nKeep StereoCrafter Studio.exe in the StereoCrafter folder, next to the .venv folder.",
                            Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        // One Studio at a time (bug sweep 2026-10-09, A1): when one is running,
        // studio.single_instance brings its window forward and exits 3 -- and
        // this launcher leaves the running Studio's log alone.
        try
        {
            ProcessStartInfo check = new ProcessStartInfo(pythonw, "-m studio.single_instance");
            check.WorkingDirectory = root;
            check.UseShellExecute = false;
            check.CreateNoWindow = true;
            using (Process c = Process.Start(check))
            {
                // A minute, not 15 s: going on while the check still ran started a second
                // Studio, whose log truncated the running one's (bug sweep 2026-10-14c). Still
                // no answer: nothing started, and nothing of the running one's touched.
                if (!c.WaitForExit(60000))
                {
                    try { c.Kill(); } catch (Exception) { }
                    MessageBox.Show("StereoCrafter Studio couldn't tell whether it is already running " +
                                    "(the check took over a minute).\n\nTry again in a moment.",
                                    Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return 1;
                }
                if (c.ExitCode == 3)
                    return 0;
            }
        }
        catch (Exception) { }

        // The previous run's log is kept beside it (Studio audit 2026-09-28):
        // relaunching after a crash overwrote the one log that explained it.
        try { if (File.Exists(log)) File.Copy(log, Path.Combine(root, "studio_last_run.prev.log"), true); }
        catch (Exception) { }

        int code;
        try
        {
            using (StreamWriter writer = new StreamWriter(log, false, new UTF8Encoding(false)))
            {
                writer.AutoFlush = true;
                object gate = new object();
                ProcessStartInfo info = new ProcessStartInfo(pythonw, "-u -m studio.app");
                info.WorkingDirectory = root;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                info.StandardOutputEncoding = Encoding.UTF8;
                info.StandardErrorEncoding = Encoding.UTF8;
                using (Process p = new Process())
                {
                    p.StartInfo = info;
                    bool closed = false;
                    DataReceivedEventHandler write = delegate(object s, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        lock (gate) { if (!closed) writer.WriteLine(e.Data); }
                    };
                    p.OutputDataReceived += write;
                    p.ErrorDataReceived += write;
                    p.Start();
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    // Studio's own exit, not the pipes' end: the parameterless
                    // WaitForExit also waits for the redirected output to close,
                    // so a child still holding it (an orphaned encoder) kept this
                    // launcher waiting, invisibly, until that child ended (Studio
                    // audit 2026-09-28: 6 s child -> 6 s wait; now 0.4 s). The last
                    // lines get a moment to land, then the log is closed.
                    p.WaitForExit(int.MaxValue);
                    System.Threading.Thread.Sleep(300);
                    lock (gate) { closed = true; }
                    code = p.ExitCode;
                }
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show("Studio could not be started:\n\n" + ex.Message, Title,
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        if (code != 0)
        {
            DialogResult answer = MessageBox.Show(
                "StereoCrafter Studio exited abnormally (code " + code + ").\n\nOpen the log?\n" + log,
                Title, MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (answer == DialogResult.Yes)
            {
                try { Process.Start("notepad.exe", "\"" + log + "\""); } catch (Exception) { }
            }
        }
        return code;
    }
}
