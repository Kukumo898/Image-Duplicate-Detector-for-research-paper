using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("图片查重系统")]
[assembly: AssemblyDescription("图片查重系统 · 本地科研图片核验")]
[assembly: AssemblyProduct("图片查重系统")]
[assembly: AssemblyVersion("1.0.1.0")]
[assembly: AssemblyFileVersion("1.0.1.0")]

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string key;
        using (var sha = SHA256.Create()) key = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-", "").Substring(0, 24);
        bool owner;
        using (var mutex = new Mutex(true, "Local\\Tujian.Gui." + key, out owner))
        using (var openEvent = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\Tujian.Open." + key))
        using (var stopEvent = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\Tujian.Stop." + key))
        {
            string action = args.Length == 1 && args[0] == "--stop" ? "stop" : "start";
            if (!owner) { (action == "stop" ? stopEvent : openEvent).Set(); return; }
            try { Application.Run(new LauncherForm(root, openEvent, stopEvent, action)); }
            catch (Exception ex) { MessageBox.Show("图片查重系统入口未能完成操作。\n" + ex.Message, "图片查重系统", MessageBoxButtons.OK, MessageBoxIcon.Information); }
            finally { mutex.ReleaseMutex(); }
        }
    }
}

internal sealed class CommandResult
{
    public int Code;
    public string Output;
    public string Error;
}

internal sealed class LauncherForm : Form
{
    readonly string root;
    readonly EventWaitHandle openEvent, stopEvent;
    readonly string initialAction;
    readonly NotifyIcon tray;
    readonly System.Windows.Forms.Timer signals;
    readonly Label heading, status, detail;
    readonly ProgressBar progress;
    readonly Button retryButton, logsButton, dismissButton;
    readonly ToolStripMenuItem openItem, stopItem, backupItem, restoreItem;
    bool busy, exiting;
    string lastAction = "start";
    readonly Color blue = Color.FromArgb(37, 99, 235);

    public LauncherForm(string packageRoot, EventWaitHandle open, EventWaitHandle stop, string action)
    {
        root = packageRoot; openEvent = open; stopEvent = stop; initialAction = action;
        Text = "图片查重系统";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Font = new Font("Microsoft YaHei UI", 10f);
        ClientSize = new Size(480, 240);
        AutoScaleMode = AutoScaleMode.Dpi;
        BackColor = Color.White;
        FormBorderStyle = FormBorderStyle.FixedSingle;
        StartPosition = FormStartPosition.CenterScreen;
        MaximizeBox = false;
        MinimizeBox = true;
        var logo = new PictureBox { Location = new Point(30, 32), Size = new Size(68, 68), SizeMode = PictureBoxSizeMode.Zoom, Image = Image.FromStream(Assembly.GetExecutingAssembly().GetManifestResourceStream("tujian.png")) };
        Controls.Add(logo);
        heading = new Label { Text = "图片查重系统", AutoSize = false, Location = new Point(117, 29), Size = new Size(310, 40), Font = new Font("Microsoft YaHei UI", 23f, FontStyle.Bold), ForeColor = Color.FromArgb(26, 39, 64) };
        Controls.Add(heading);
        Controls.Add(new Label { Text = "科研图片核验 · 本机运行", Location = new Point(120, 76), Size = new Size(310, 28), ForeColor = Color.FromArgb(112, 126, 148) });
        status = new Label { Text = "正在打开工作台…", Location = new Point(30, 126), Size = new Size(420, 26), ForeColor = Color.FromArgb(45, 61, 84) };
        Controls.Add(status);
        progress = new ProgressBar { Location = new Point(30, 163), Size = new Size(420, 5), Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 25 };
        Controls.Add(progress);
        detail = new Label { Text = "就绪后会自动打开浏览器", Location = new Point(30, 184), Size = new Size(420, 26), ForeColor = Color.FromArgb(112, 126, 148), AutoEllipsis = true };
        Controls.Add(detail);
        retryButton = MakeButton("重试", 30, 242, delegate { StartOperation(lastAction); });
        logsButton = MakeButton("查看日志", 155, 242, delegate { OpenLogs(); });
        dismissButton = MakeButton("收起", 280, 242, delegate { Hide(); });
        retryButton.BackColor = blue; retryButton.ForeColor = Color.White;
        var menu = new ContextMenuStrip();
        openItem = new ToolStripMenuItem("打开图片查重系统", null, delegate { StartOperation("start"); });
        stopItem = new ToolStripMenuItem("关闭图片查重系统并退出", null, delegate { StartOperation("stop"); });
        backupItem = new ToolStripMenuItem("备份资料…", null, delegate { Shell(Path.Combine(Directory.GetParent(root).FullName, "迁移数据.exe")); });
        restoreItem = new ToolStripMenuItem("恢复备份到新目录…", null, delegate { Shell(Path.Combine(Directory.GetParent(root).FullName, "迁移数据.exe")); });
        menu.Items.Add(openItem);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(backupItem); menu.Items.Add(restoreItem);
        menu.Items.Add(new ToolStripMenuItem("使用说明", null, delegate { Shell(Path.Combine(Directory.GetParent(root).FullName, "使用说明.txt")); }));
        menu.Items.Add(new ToolStripSeparator()); menu.Items.Add(stopItem);
        menu.Items.Add(new ToolStripMenuItem("仅退出图标（服务保持运行）", null, delegate {
            if (busy) { NotifyBusy(); return; }
            exiting = true; Close();
        }));
        tray = new NotifyIcon { Icon = Icon, Text = "图片查重系统 · 本机工作台", ContextMenuStrip = menu, Visible = true };
        tray.DoubleClick += delegate { StartOperation("start"); };
        signals = new System.Windows.Forms.Timer { Interval = 250 };
        signals.Tick += delegate {
            if (stopEvent.WaitOne(0)) { if (!busy) StartOperation("stop"); else NotifyBusy(); }
            if (openEvent.WaitOne(0)) { if (!busy) StartOperation("start"); else { Show(); Activate(); } }
        };
        signals.Start();
        Shown += delegate { StartOperation(initialAction); };
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if (!exiting && e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; Hide(); tray.ShowBalloonTip(2500, "图片查重系统仍在后台", "右下角图片查重系统图标可打开工作台或正常关闭。", ToolTipIcon.Info); }
        };
        FormClosed += delegate { signals.Dispose(); tray.Visible = false; tray.Dispose(); };
    }

    Button MakeButton(string text, int x, int y, EventHandler handler)
    {
        var button = new Button { Text = text, Location = new Point(x, y), Size = new Size(112, 34), FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(244, 247, 252), Visible = false };
        button.FlatAppearance.BorderSize = 0; button.Click += handler; Controls.Add(button); return button;
    }

    void NotifyBusy() { tray.ShowBalloonTip(2200, "图片查重系统正在处理", "请等待当前操作完成。不会强行结束检测或覆盖资料。", ToolTipIcon.Info); }

    async void StartOperation(string action)
    {
        if (busy) { NotifyBusy(); return; }
        busy = true; lastAction = action; UpdateEnabled(false);
        string archive = null, target = null;
        if (action == "restore")
        {
            try {
            using (var dialog = new OpenFileDialog { Title = "选择图片查重系统备份", Filter = "图片查重系统备份 ZIP|*.zip", InitialDirectory = Directory.Exists(Path.Combine(root, "backups")) ? Path.Combine(root, "backups") : root })
            { if (dialog.ShowDialog(this) != DialogResult.OK) { busy = false; UpdateEnabled(true); return; } archive = dialog.FileName; }
            target = Path.Combine(Directory.GetParent(root).FullName, "图片查重系统_恢复_" + DateTime.Now.ToString("yyyyMMdd_HHmmss"));
            } catch (Exception ex) { ShowFailure(action, ex.Message); busy = false; UpdateEnabled(true); return; }
        }
        ClientSize = new Size(480, 240); retryButton.Visible = logsButton.Visible = dismissButton.Visible = false;
        progress.Visible = true; detail.Location = new Point(30, 184); detail.Size = new Size(420, 26); detail.Text = action == "start" ? "就绪后会自动打开浏览器" : "资料会完整保留，请稍候";
        status.Text = action == "start" ? "正在打开工作台…" : action == "stop" ? "正在安全关闭图片查重系统…" : action == "backup" ? "正在准备资料备份…" : "正在恢复到新目录…";
        Show(); WindowState = FormWindowState.Normal; Activate();
        try
        {
            // Backups remain offline. Stop through the existing atomic admission gate,
            // never kill a process; busy operations therefore reject the backup safely.
            if (action == "backup") Ensure(await StopSafely());
            CommandResult result = action == "stop" ? await StopSafely() : await RunCommand(action, archive, target);
            Ensure(result);
            if (action == "start")
            {
                var config = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(root, "daily_profile.json"), Encoding.UTF8));
                int port = Convert.ToInt32(config["port"]);
                if (port < 1024 || port > 65535) throw new InvalidOperationException("网页端口设置无效。");
                string url = "http://127.0.0.1:" + port;
                status.Text = "工作台已就绪"; detail.Text = url;
                try { Shell(url); }
                catch { throw new InvalidOperationException("图片查重系统已启动，但浏览器未能打开。请在浏览器输入：" + url); }
                Log("start-ready", "port=" + port);
                Hide();
            }
            else if (action == "stop")
            {
                Log("stop-complete", "cooperative shutdown");
                exiting = true; Close();
            }
            else if (action == "backup")
            {
                Hide(); MessageBox.Show("备份已保存到本程序的 backups 文件夹。\n\n图片查重系统已正常关闭；需要使用时双击图标重新打开。", "备份完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
                exiting = true; Close();
            }
            else
            {
                // The existing restorer copies all package-root files, including this EXE.
                if (!File.Exists(Path.Combine(target, "图鉴.exe"))) throw new InvalidOperationException("资料已恢复，但新目录没有图片查重系统图标入口，请查看日志。恢复目录：" + target);
                Hide(); MessageBox.Show("资料已恢复到：\n" + target + "\n\n请双击新目录中的“图片查重系统.exe”。原资料未覆盖。", "恢复完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
                Shell(target);
            }
        }
        catch (Exception ex) { ShowFailure(action, ex.Message); }
        finally { busy = false; if (!IsDisposed) UpdateEnabled(true); }
    }

    void UpdateEnabled(bool enabled) { openItem.Enabled = stopItem.Enabled = backupItem.Enabled = restoreItem.Enabled = retryButton.Enabled = enabled; }
    static void Ensure(CommandResult result)
    {
        if (result.Code != 0) throw new InvalidOperationException(String.IsNullOrWhiteSpace(result.Error) ? result.Output : result.Error);
    }

    async Task<CommandResult> StopSafely()
    {
        var result = await RunCommand("stop", null, null);
        // Windows can deny image-name lookup during the brief process teardown
        // interval. Re-run the ENTIRE authenticated ownership check once; never
        // interpret access-denied itself as success and never retry busy refusal.
        if (result.Code != 0 && result.Error.Contains("[WinError 5]"))
        {
            await Task.Delay(650);
            result = await RunCommand("stop", null, null);
        }
        return result;
    }

    async Task<CommandResult> RunCommand(string action, string archive, string target)
    {
        string python = Path.Combine(root, "runtime", "python", "python.exe");
        string script = Path.Combine(root, "tools", "daily_launcher.py");
        if (!File.Exists(python) || !File.Exists(script)) throw new FileNotFoundException("软件文件不完整。请将完整“图片查重系统”文件夹解压后使用，不要单独移动图片查重系统.exe。");
        string arguments = "-B -X utf8 " + Quote(script) + " " + action + " --profile " + Quote(Path.Combine(root, "daily_profile.json"));
        if (archive != null) arguments += " --archive " + Quote(archive) + " --target " + Quote(target);
        var info = new ProcessStartInfo(python, arguments) { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
        foreach (string key in info.EnvironmentVariables.Keys.Cast<string>().ToArray()) if (key.StartsWith("LABIMAGE_", StringComparison.OrdinalIgnoreCase)) info.EnvironmentVariables.Remove(key);
        info.EnvironmentVariables.Remove("BROWSER");
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
        info.EnvironmentVariables["LABIMAGE_NO_BROWSER"] = "1";
        using (var process = new Process { StartInfo = info })
        {
            process.Start();
            Task<string> stdout = process.StandardOutput.ReadToEndAsync();
            Task<string> stderr = process.StandardError.ReadToEndAsync();
            await Task.Run(() => process.WaitForExit());
            var result = new CommandResult { Code = process.ExitCode, Output = await stdout, Error = await stderr };
            Log(action + " exit=" + result.Code, result.Output + "\n" + result.Error);
            return result;
        }
    }

    static string Quote(string value)
    {
        // CommandLineToArgvW-compatible escaping; no shell interprets these arguments.
        var b = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') b.Append('\\', slashes * 2 + 1); else b.Append('\\', slashes);
            slashes = 0; b.Append(c);
        }
        b.Append('\\', slashes * 2); b.Append('"'); return b.ToString();
    }

    void ShowFailure(string action, string message)
    {
        Log("gui-failure " + action, message);
        status.Text = action == "stop" ? "暂时无法关闭图片查重系统" : action == "start" ? "图片查重系统暂时未能打开" : "本次操作未完成";
        progress.Visible = false;
        string friendly = message.Trim();
        if (friendly.Length > 360) friendly = friendly.Substring(0, 360) + "…";
        detail.Text = friendly; detail.AutoEllipsis = true; detail.Size = new Size(420, 124); detail.Location = new Point(30, 163);
        ClientSize = new Size(480, 356);
        retryButton.Location = new Point(30, 305); logsButton.Location = new Point(155, 305); dismissButton.Location = new Point(280, 305);
        retryButton.Visible = logsButton.Visible = dismissButton.Visible = true;
        Show(); WindowState = FormWindowState.Normal; Activate();
    }

    void Log(string action, string text)
    {
        try { File.AppendAllText(Path.Combine(root, "desktop_launcher.log"), DateTime.Now.ToString("s") + " " + action + "\n" + text + "\n", new UTF8Encoding(false)); } catch { }
    }
    void OpenLogs() { try { Shell(Path.Combine(root, "desktop_launcher.log")); } catch (Exception e) { MessageBox.Show(e.Message, "图片查重系统"); } }
    static void Shell(string path) { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
}
