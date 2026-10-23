# Installation and Usage

## Download

The current packaged release is intended for **Windows x64**.

1. Open the [v1.0.0 release page](https://github.com/Kukumo898/Image-Duplicate-Detector-for-research-paper/releases/tag/v1.0.0).
2. Under **Assets**, download the Windows x64 ZIP package. Do **not** choose the automatically generated `Source code (zip)` or `Source code (tar.gz)` archives: those contain the repository contents, not the packaged Windows application.
3. Extract the **entire** ZIP package to a new local folder. Do not run the launcher from inside the compressed archive.
4. Keep the files and folders together as distributed.
5. Read `使用说明.txt`, then double-click `启动图片查重系统.exe`.
6. The launcher opens a local workspace in your browser after initialization.

## Package Layout

The Windows ZIP package contains the following items:

| Item | Purpose |
| --- | --- |
| `启动图片查重系统.exe` | Launches the image inspection application |
| `迁移数据.exe` | Data migration utility; use only when needed and in accordance with the included instructions |
| `程序文件/` | Supporting application files; keep this folder intact |
| `使用说明.txt` | Original Chinese user guide |

## Main Inspection Modes

The application's interface provides:

- **Batch inspection**: check selected image files, with an option to compare against a local research image library.
- **Document inspection**: inspect images extracted from PDF and Word documents.
- **Two-image comparison**: compare a selected pair of images in detail.
- **Inspection history**: review previous tasks and recorded evidence.
- **Research image library**: maintain a local image collection for cross-task comparison.

In the batch file-selection interface, the displayed image formats are PNG, JPG, TIFF, BMP and WebP. The currently displayed limits are 100 files per task, up to 100 MB per file and up to 500 MiB in total. These are application-interface limits rather than tested minimum hardware specifications and may vary with future versions.

## System Requirements

- **Platform**: Windows, 64-bit (x64) release.
- **Browser**: an available browser for the local workspace launched by the application.
- **Other requirements**: minimum Windows version, RAM, storage after extraction, and CPU requirements have not yet been independently verified or published. They should be added only after testing.

## Troubleshooting

**The application does not start**

- Confirm that the archive was fully extracted and `程序文件/` is beside the launcher.
- Try a short, writable local folder path rather than running directly inside the ZIP or a restricted system directory.
- If Windows displays a security warning, verify the package source and scan the file. Do not disable antivirus protection just to run it.
- Report your Windows version, the release version and the exact error message in a GitHub Issue.

**The launcher runs but no browser window appears**

- Give initialization time to complete, then check for a newly opened browser tab.
- Check whether the application displayed an error. Do not assume a publicly accessible server is being started; the workspace is intended to run locally.

**Images cannot be imported**

- Check the displayed format and file-size restrictions.
- Test with a non-sensitive sample file first, and keep a note of the exact reproduction steps.

## Scientific and Privacy Notice

This software is an auxiliary screening tool, not an automated determination of research misconduct. Potentially matched regions require human review against the original images, figure preparation history and experimental context.

Before sharing bug reports, screenshots or data, remove unpublished figures, patient identifiers, credentials and other confidential information. Do not upload sensitive research data to public GitHub Issues.

## Feedback

Please open a [GitHub Issue](https://github.com/Kukumo898/Image-Duplicate-Detector-for-research-paper/issues) with the Windows version, software version, steps to reproduce, expected behavior and observed behavior. Attach only public or synthetic example images.
