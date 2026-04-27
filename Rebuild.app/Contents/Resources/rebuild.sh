#!/bin/bash
osascript << 'AS'
tell application "Terminal"
    activate
    do script "bash /Users/uplift-user/Developer/uplift/build.sh"
end tell
AS
