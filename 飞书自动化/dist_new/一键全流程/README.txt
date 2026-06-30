Package output of 一键全流程.py
Double-click 一键全流程.exe to launch the GUI.
Or open PowerShell here and use CLI args (--cli / --only / --skip / --strict / --no-clean / --force-clean).

GUI vs CLI dispatch:
  - No CLI args   -> GUI mode (Tkinter window)
  - --cli         -> CLI default (run all 4 steps + cleanup)
  - --only / --skip / others -> CLI mode (backwards compatible)

Layout:
  一键全流程.exe          main program (CLI orchestrator)
  _internal\              PyInstaller runtime deps, DO NOT DELETE
  个人码相关\             4 step scripts (editable, restart exe to apply)
    飞书保存合伙宝妈个人码2.py   Step 1: download QR + generate ledger txt
    批量执行动作_二维码.py      Step 2: OpenCV preprocess QR images
    生成个人码3.py              Step 3: PSD compose personal QR3
    个人码回写\                  Step 4 module dir
      main.py / config.py / feishu_api.py / file_parser.py /
      logger.py / encoding_utils.py

Common CLI usage (open PowerShell in this dir):
  .\一键全流程.exe                          # run all 4 steps + cleanup
  .\一键全流程.exe --only 1,4               # run only step 1 and 4
  .\一键全流程.exe --skip 3                 # skip step 3
  .\一键全流程.exe --strict                 # stop on first failure
  .\一键全流程.exe --no-clean               # do not clean intermediate files
  .\一键全流程.exe --force-clean            # cleanup even when previous steps failed

Editing tips:
  - Feishu APP_ID / APP_SECRET / APP_TOKEN / TABLE_ID
      → 个人码相关\飞书保存合伙宝妈个人码2.py / 个人码相关\个人码回写\config.py
  - PSD template path / output dir
      → 个人码相关\生成个人码3.py (PSD_PATH / OUTPUT_DIR / BACKUP_DIR)
  - Cleanup target list / backup directory
      → 一键全流程.exe is built from 一键全流程.py; CLEANUP_TARGETS
        is baked into the exe. Edit source + repackage if needed.
