@echo off
REM Litminer Agent-facing API-first workflow.
REM Usage: pipeline.bat "your search query" YEAR [OPENALEX_API_KEY] [fast|balanced|full]

set "QUERY=%~1"
set "YEAR=%~2"
set "KEY=%~3"
set "MODE=%~4"
set "OUT=.litminer\runs"
set "PYTHON_CMD=python"

if "%QUERY%"=="" goto :usage
if "%YEAR%"=="" goto :usage
if "%MODE%"=="" set "MODE=fast"
where python >nul 2>nul
if %errorlevel% neq 0 (
  where py >nul 2>nul
  if %errorlevel% neq 0 goto :python_error
  set "PYTHON_CMD=py -3"
)
if not exist "%OUT%" mkdir "%OUT%"

echo ========================================
echo Litminer API-First Pipeline
echo Query: %QUERY%
echo Year:  %YEAR%+
echo Mode:  %MODE%
echo ========================================
echo.

echo [1/1] Run Agent-facing workflow...
if "%KEY%"=="" (
  %PYTHON_CMD% -m litminer.engine.run_lit_search --mode %MODE% --query "%QUERY%" --year-from %YEAR% --output-dir "%OUT%\litminer_run"
) else (
  %PYTHON_CMD% -m litminer.engine.run_lit_search --mode %MODE% --query "%QUERY%" --year-from %YEAR% --output-dir "%OUT%\litminer_run" --openalex-api-key "%KEY%"
)
if %errorlevel% neq 0 goto :error

echo.
echo ========================================
echo Pipeline Complete
echo Output files in %OUT%\litminer_run\:
echo   api_candidates.csv
echo   api_discovery_trace.csv
echo   api_discovery_report.md
echo   triaged_candidates.csv
echo   selected_candidates.csv
echo   verified_candidates.csv
echo   oa_annotated_candidates.csv
echo   publisher_queue.csv
echo   publisher_queue_validation.md
echo   feasibility_report.md
echo   processing_report.md
echo   agent_summary.json
echo ========================================
goto :end

:usage
echo Usage: pipeline.bat "your search query" YEAR [OPENALEX_API_KEY] [fast^|balanced^|full]
goto :end

:python_error
echo.
echo PYTHON NOT FOUND: install Python 3.10+ or add python/py to PATH.
goto :end

:error
echo.
echo PIPELINE FAILED at step with exit code %errorlevel%
pause

:end
