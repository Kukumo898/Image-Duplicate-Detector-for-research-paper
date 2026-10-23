using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using System.Collections.Generic;

[assembly: AssemblyTitle("图片查重系统")]
[assembly: AssemblyProduct("图片查重系统")]
[assembly: AssemblyVersion("1.1.0.0")]

static class DeliveryEntry
{
    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string outer = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string inner = Path.Combine(outer, "程序文件");
        try
        {
            if (!Directory.Exists(inner)) throw new InvalidOperationException("请保留完整的图片查重系统文件夹。启动入口需要与“程序文件”放在一起，不能单独移动。");
            if (Path.GetFileNameWithoutExtension(Application.ExecutablePath).StartsWith("迁移"))
                Application.Run(new MigrationForm(outer, inner));
            else
            {
                string target = Path.Combine(inner, "图鉴.exe");
                if (!File.Exists(target)) throw new FileNotFoundException("程序文件不完整，请重新解压完整的软件包。");
                Process.Start(new ProcessStartInfo(target) { WorkingDirectory = inner, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden });
            }
        }
        catch (Exception e) { MessageBox.Show(e.Message, "图片查重系统", MessageBoxButtons.OK, MessageBoxIcon.Information); }
    }
}

sealed class MigrationForm : Form
{
    readonly string outer, inner;
    readonly Label status;
    readonly Button exportButton, importButton, legacyButton;
    readonly ProgressBar progress;
    bool busy;
    public MigrationForm(string outerRoot, string innerRoot)
    {
        outer = outerRoot; inner = innerRoot;
        Text = "图片查重系统 · 迁移数据";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Font = new Font("Microsoft YaHei UI", 10f);
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(560, 370);
        BackColor = Color.White; FormBorderStyle = FormBorderStyle.FixedDialog;
        StartPosition = FormStartPosition.CenterScreen; MaximizeBox = false;
        var logo = new PictureBox { Image = Image.FromStream(Assembly.GetExecutingAssembly().GetManifestResourceStream("tujian.png")), Location = new Point(28, 24), Size = new Size(62, 62), SizeMode = PictureBoxSizeMode.Zoom };
        Controls.Add(logo);
        Controls.Add(new Label { Text = "把资料带到新电脑", Location = new Point(110, 24), Size = new Size(415, 35), Font = new Font("Microsoft YaHei UI", 19f, FontStyle.Bold), ForeColor = Color.FromArgb(28, 45, 72) });
        Controls.Add(new Label { Text = "保留任务、图片、图库与复核备注", Location = new Point(112, 65), Size = new Size(415, 26), ForeColor = Color.FromArgb(108, 123, 145) });
        exportButton = Button("导出当前资料", 28, 120, delegate { BeginOperation("export", false); });
        importButton = Button("导入资料备份", 290, 120, delegate { BeginOperation("import", false); });
        Controls.Add(new Label { Text = "保存为一个资料 ZIP\n导出前会正常关闭空闲服务", Location = new Point(32, 178), Size = new Size(235, 55), ForeColor = Color.FromArgb(91, 107, 131) });
        Controls.Add(new Label { Text = "恢复到新的完整图片查重系统文件夹\n不覆盖现有资料", Location = new Point(294, 178), Size = new Size(235, 55), ForeColor = Color.FromArgb(91, 107, 131) });
        legacyButton = new Button { Text = "从旧版图片查重系统文件夹导出…", Location = new Point(28, 241), Size = new Size(245, 32), FlatStyle = FlatStyle.Flat, BackColor = Color.White, ForeColor = Color.FromArgb(37, 99, 235) };
        legacyButton.FlatAppearance.BorderSize = 0;
        legacyButton.Click += delegate { BeginOperation("export", true); }; Controls.Add(legacyButton);
        progress = new ProgressBar { Location = new Point(28, 292), Size = new Size(504, 5), Style = ProgressBarStyle.Marquee, Visible = false };
        Controls.Add(progress);
        status = new Label { Text = "资料备份是私有文件，请勿上传到公开 GitHub。", Location = new Point(28, 311), Size = new Size(504, 45), ForeColor = Color.FromArgb(108, 123, 145) };
        Controls.Add(status);
        FormClosing += delegate(object s, FormClosingEventArgs e) { if (busy && e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; status.Text = "正在完成资料操作，请稍候；不会强制中断。"; } };
    }

    Button Button(string text, int x, int y, EventHandler handler)
    {
        var button = new Button { Text = text, Location = new Point(x, y), Size = new Size(242, 49), FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(37, 99, 235), ForeColor = Color.White };
        button.FlatAppearance.BorderSize = 0; button.Click += handler; Controls.Add(button); return button;
    }

    async void BeginOperation(string operation, bool legacy)
    {
        if (busy) return;
        busy = true; exportButton.Enabled = importButton.Enabled = legacyButton.Enabled = false;
        try
        {
            string source = null, destination = null, archive = null;
            if (legacy)
                using (var dialog = new FolderBrowserDialog { Description = "选择旧版图片查重系统软件文件夹（包含程序和 user_data），不要只选数据库目录。" })
                { if (dialog.ShowDialog(this) != DialogResult.OK) return; source = dialog.SelectedPath; }
            if (operation == "export")
            {
                using (var dialog = new SaveFileDialog { Title = "把资料备份保存到哪里", Filter = "图片查重系统资料备份|*.zip", FileName = "图片查重系统资料_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".zip", OverwritePrompt = true })
                { if (dialog.ShowDialog(this) != DialogResult.OK) return; destination = dialog.FileName; }
                if (File.Exists(destination)) throw new InvalidOperationException("为避免覆盖旧备份，请选择一个新的文件名。");
            }
            else
            {
                using (var dialog = new OpenFileDialog { Title = "选择图片查重系统资料备份 ZIP", Filter = "图片查重系统资料备份|*.zip" })
                { if (dialog.ShowDialog(this) != DialogResult.OK) return; archive = dialog.FileName; }
                using (var dialog = new FolderBrowserDialog { Description = "选择恢复位置，将在其中新建完整图片查重系统文件夹，不覆盖已有资料。" })
                { if (dialog.ShowDialog(this) != DialogResult.OK) return; destination = Path.Combine(dialog.SelectedPath, "图片查重系统_资料恢复_" + DateTime.Now.ToString("yyyyMMdd_HHmmss")); }
            }
            progress.Visible = true;
            status.Text = operation == "export" ? "正在安全导出，任务忙碌时会拒绝操作…" : "正在校验并恢复到新目录…";
            var result = await Run(operation, destination, archive, source);
            status.Text = operation == "export" ? "资料已导出。" : "恢复完成，原资料未覆盖。";
            if (operation == "export") MessageBox.Show("资料已保存到：\n" + destination + "\n\n在新电脑打开“迁移数据”，选择“导入资料备份”。\n软件已正常关闭，需要使用时重新打开。", "导出完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
            else
            {
                MessageBox.Show("资料已恢复到：\n" + destination + "\n\n请双击新文件夹外面的“启动图片查重系统.exe”。", "恢复完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
                Process.Start(new ProcessStartInfo(destination) { UseShellExecute = true });
            }
        }
        catch (Exception e) { status.Text = "本次操作未完成，原资料保留。"; MessageBox.Show(e.Message, "图片查重系统 · 迁移数据", MessageBoxButtons.OK, MessageBoxIcon.Information); }
        finally { busy = false; progress.Visible = false; exportButton.Enabled = importButton.Enabled = legacyButton.Enabled = true; }
    }

    async Task<string> Run(string operation, string target, string archive, string source)
    {
        string python = Path.Combine(inner, "runtime", "python", "python.exe");
        string script = Path.Combine(inner, "tools", "layout_migration.py");
        if (!File.Exists(python) || !File.Exists(script)) throw new InvalidOperationException("程序文件不完整，请使用完整图片查重系统文件夹。");
        string args = "-B -X utf8 " + Quote(script) + " " + operation;
        args += operation == "export" ? " --output " + Quote(target) : " --archive " + Quote(archive) + " --target " + Quote(target);
        if (source != null) args += " --source-package " + Quote(source);
        var psi = new ProcessStartInfo(python, args) { UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden, WorkingDirectory = inner, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
        foreach (string key in psi.EnvironmentVariables.Keys.Cast<string>().ToArray()) if (key.StartsWith("LABIMAGE_", StringComparison.OrdinalIgnoreCase)) psi.EnvironmentVariables.Remove(key);
        psi.EnvironmentVariables["PYTHONUTF8"] = "1"; psi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"; psi.EnvironmentVariables["LABIMAGE_NO_BROWSER"] = "1";
        using (var p = Process.Start(psi))
        {
            var stdout = p.StandardOutput.ReadToEndAsync(); var stderr = p.StandardError.ReadToEndAsync();
            await Task.Run(() => p.WaitForExit());
            string output = await stdout, error = await stderr;
            try { File.AppendAllText(Path.Combine(inner, "migration_entry.log"), DateTime.Now.ToString("s") + " " + operation + " exit=" + p.ExitCode + "\n" + output + "\n" + error + "\n", new UTF8Encoding(false)); } catch { }
            if (p.ExitCode != 0) throw new InvalidOperationException(String.IsNullOrWhiteSpace(error) ? output : error);
            return output;
        }
    }
    static string Quote(string value)
    {
        var b = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) { if (c == '\\') { slashes++; continue; } b.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); slashes = 0; b.Append(c); }
        b.Append('\\', slashes * 2); b.Append('"'); return b.ToString();
    }
}
