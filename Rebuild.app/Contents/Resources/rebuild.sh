#!/bin/bash
osascript << 'AS'
tell application "Terminal"
    activate
    do script "bash /Users/uplift-user/Developer/drive-uploader/build.sh"
end tell
AS
