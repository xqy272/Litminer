@echo off
REM Litminer Agent-facing API-first workflow.
REM Usage: pipeline.bat "your search query" YEAR [OPENALEX_API_KEY]

set "QUERY=%~1"
set "YEAR=%~2"
set "KEY=%~3"
set "OUT=.litminer\runs"

if "%QUERY%"=="" goto :usage
if "%YEAR%"=="" goto :usage
if not exist "%OUT%" mkdir "%OUT%"

echo ========================================
echo Litminer API-First Pipeline
echo Query: %QUERY%
echo Year:  %YEAR%+
echo ========================================
echo.

echo [1/1] Run Agent-facing workflow...
if "%KEY%"=="" (
  python -m litminer.engine.run_lit_search --query "%QUERY%" --year-from %YEAR% --output-dir "%OUT%\litminer_run" --include-semantic-scholar
) else (
  python -m litminer.engine.run_lit_search --query "%QUERY%" --year-from %YEAR% --output-dir "%OUT%\litminer_run" --openalex-api-key "%KEY%" --include-semantic-scholar
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
echo ========================================
goto :end

:usage
echo Usage: pipeline.bat "your search query" YEAR [OPENALEX_API_KEY]
goto :end

:error
echo.
echo PIPELINE FAILED at step with exit code %errorlevel%
pause

:end
