#!/bin/bash
# Assert that the command actually ran and created the marker file
test -f /tmp/terrible_marker.txt
