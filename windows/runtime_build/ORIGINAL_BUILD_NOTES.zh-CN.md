# 随包运行时构建与定向复现

## 构建输入与结果

本目录只用于维护人员重建；不随发行包运行。构建机 Python 是编译／下载工具，**不是交付运行依赖**。

输入：

1. 应用基线 `output/frontend_refresh/release_source/requirements-lock.txt`，复制后与包内锁保持相同字节及哈希。
2. python.org 官方 `python-3.14.0-embed-amd64.zip`，官方发行页 MD5 `7c5d8d8e3213a11bd0e36f8b8eb03431`；记录本地 SHA-256 和官方 SPDX SBOM。
3. PyPI 上同版本的 20 个 wheel；各 wheel 的 SHA-256 与 PyPI JSON 元数据逐项匹配。下载缓存只在构建目录，交付包不依赖 pip 缓存。
4. 官方 VS 2022 Build Tools 的 x64 CRT REDIST 文件；目录版本 14.44.35112，实际 DLL 版本 14.44.35211.0。所有 DLL 原样复制，签名验证均 Valid。

输出：`dist/图鉴/runtime/python/`，包根依赖／原生清单和 `LICENSES/`。不复制 `.venv`、不改原服务、不复制开发 Python，不向交付包注入绝对运行路径。

## 重建

在项目根目录用 **Python 3.14 x64 构建解释器**执行（构建时需要网络，但交付运行不需要）：

```powershell
& '<构建机 Python 3.14 x64>/python.exe' 'output/portable_release/work/runtime/build_runtime.py'
& '<构建机 Python>/python.exe' 'output/portable_release/work/runtime/finalize_runtime_metadata.py'
```

如果构建机的 VS Redist 位置不同，显式加 `--vc-redist '<官方 x64 Microsoft.VC143.CRT 目录>'`。不得指向 System32 或随意下载 DLL 网站。重建器只覆盖自己负责的随包运行时和清单，不处理当前用户数据，不需要卸载或隐藏本机 Python。

`wheels/` 与官方 Python ZIP 是可审计重建输入；`.pypi.json` 记录 PyPI 对应版本；这些都在包外 `work/runtime/`，不必交给普通用户。

## 用最终产物定向检查

```powershell
& '<待验收包>/runtime/python/python.exe' -B `
  'output/portable_release/work/runtime/audit_runtime.py' `
  '<待验收包>' 'output/portable_release/validation/runtime_audit_moved.json'
```

脚本只在审计 JSON 旁产生小型、固定随机种子的合成素材，不写用户任务库，不启动服务。它实际检查 PNG/JPEG、OpenCV SIFT、NumPy linalg、SQLite，以及应用现有 `ingest_file` PDF／DOCX 链；PDF 应得 2 个表示（整页＋内嵌图），DOCX 应得 1 张图。

同时通过 Windows EnumProcessModules 记录当前进程已加载 DLL/PYD/EXE，及 Python 模块的真实位置。只有包内模块和明确 Windows 系统组件可以通过。即便来自 System32，`msvcp140.dll` 等第三方 VC Redistributable 也判为遗漏依赖；Windows 自带 `msvcp_win.dll` 不属于该遗漏。

初次审计发现系统 `msvcp140.dll` 被加载，因而补入官方 app-local CRT 后再验；补齐后 `runtime_audit_original.json` 为 `passed: true`，78 个已加载 native 模块，566 个 Python 文件模块，包外非系统依赖均为 0。`runtime_dll_signatures.json` 保存随包根目录 DLL 的签名、准确版本及哈希。

运行时检查不等于完整产品验收：启动入口、真实任务、开关生命周期、迁移及备份仍由包外最终验收报告和对应证据验证。此环境没有干净的第二台 Windows，不宣称已经证明任意 Windows 跨机可用。
