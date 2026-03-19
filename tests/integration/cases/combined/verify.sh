#!/usr/bin/env bash
set -e
test -f /tmp/terrible_marker.txt
test -d /tmp/terrible_test_dir
test -f /tmp/terrible_async_marker.txt
test -f /tmp/terrible_delegate_marker.txt
test -f /tmp/terrible_datasource_stat_test
