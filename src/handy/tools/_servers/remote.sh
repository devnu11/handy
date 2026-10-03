# handy servers: remote side.
#
# Piped into `sh -s` over ssh. Defines functions only; the caller appends one
# call per server. POSIX sh plus common userland: no bash, no python, no handy
# needed on the remote host. Nothing here may read stdin, which is this script.
#
# Every per-server function reports with exactly one line:
#   RESULT <TAB> name <TAB> state <TAB> mode <TAB> pid <TAB> uptime <TAB> detail
#
# Functions share sh's global namespace, so each uses its own variable prefix.
# Process groups are signalled as `kill -SIG -PGID`: dash rejects `kill --`.

HS_ROOT="$HOME/.local/state/handy-servers/hosts"
HS_GRACE=10            # seconds a fresh process may take to open its port
HS_LOCK_STALE_MIN=2    # minutes before an abandoned start lock is broken

hs_result() {
	printf 'RESULT\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6"
}

hs_quote() {
	printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

hs_dir() {
	printf '%s/%s' "$HS_ROOT" "$1"
}

# Load a pid file into hs_pid hs_started hs_mode hs_group.
hs_read() {
	hs_pid='' hs_started='' hs_mode='' hs_group=''
	[ -f "$1" ] || return 1
	read -r hs_pid hs_started hs_mode hs_group <"$1"
}

# Is the recorded process (or, for a process group, any member) alive?
hs_alive() {
	case $hs_pid in '' | 0 | *[!0-9]*) return 1 ;; esac
	if [ "$hs_group" = 1 ]; then
		kill -0 "-$hs_pid" 2>/dev/null
	else
		kill -0 "$hs_pid" 2>/dev/null
	fi
}

hs_signal() {
	if [ "$hs_group" = 1 ]; then
		kill -"$1" "-$hs_pid" 2>/dev/null
	else
		command -v pkill >/dev/null 2>&1 && pkill -"$1" -P "$hs_pid" 2>/dev/null
		kill -"$1" "$hs_pid" 2>/dev/null
	fi
}

# 0: something listens on the port. 1: nothing does. 2: no way to tell.
hs_listening() {
	if command -v ss >/dev/null 2>&1; then
		ss -ltn </dev/null 2>/dev/null | awk -v p=":$1" '
			NR > 1 { a = $4; if (substr(a, length(a) - length(p) + 1) == p) f = 1 }
			END { exit !f }'
		return
	fi
	if command -v lsof >/dev/null 2>&1; then
		lsof -nP -iTCP:"$1" -sTCP:LISTEN </dev/null >/dev/null 2>&1
		return
	fi
	if command -v nc >/dev/null 2>&1; then
		nc -z -w 1 127.0.0.1 "$1" </dev/null >/dev/null 2>&1
		return
	fi
	if command -v bash >/dev/null 2>&1; then
		bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" </dev/null >/dev/null 2>&1
		return
	fi
	return 2
}

# Work out a server's state into hs_state hs_uptime hs_detail (plus hs_read's).
hs_state() {
	_s_dir=$(hs_dir "$3")
	hs_read "$_s_dir/$1.pid"
	hs_state='' hs_uptime='' hs_detail=''
	if hs_alive; then
		hs_uptime=$(($(date +%s) - ${hs_started:-0}))
		hs_listening "$2"
		case $? in
		0) hs_state=running ;;
		2) hs_state=running hs_detail='no port probe (ss, lsof, nc, bash) on this host' ;;
		*)
			if [ "$hs_uptime" -lt "$HS_GRACE" ]; then
				hs_state=starting
			else
				hs_state=unhealthy hs_detail="process alive but port $2 is not listening"
			fi
			;;
		esac
	else
		hs_pid='' hs_mode='' hs_group=''
		if hs_listening "$2"; then
			hs_state=port-conflict hs_detail="port $2 is held by a process handy didn't start"
		elif [ -f "$_s_dir/$1.held" ]; then
			hs_state=stopped hs_detail='stopped on purpose; start it by name to resume'
		else
			hs_state=down
		fi
	fi
}

hs_report() {
	hs_result "$1" "$hs_state" "$hs_mode" "$hs_pid" "$hs_uptime" "$hs_detail"
}

hs_status() { # name port host
	hs_state "$1" "$2" "$3"
	hs_report "$1"
}

hs_start() { # name port host mode command resume
	_st_name=$1 _st_port=$2 _st_host=$3 _st_mode=$4 _st_cmd=$5 _st_resume=$6
	_st_dir=$(hs_dir "$_st_host")
	if ! mkdir -p "$_st_dir" 2>/dev/null; then
		hs_result "$_st_name" failed "$_st_mode" '' '' "cannot create $_st_dir"
		return
	fi

	# The lock that actually prevents duplicates: every machine and session
	# starting this server goes through it.
	_st_lock=$_st_dir/$_st_name.lock
	if ! mkdir "$_st_lock" 2>/dev/null; then
		if [ -n "$(find "$_st_lock" -prune -mmin +"$HS_LOCK_STALE_MIN" 2>/dev/null)" ]; then
			rm -rf "$_st_lock"
		fi
		if ! mkdir "$_st_lock" 2>/dev/null; then
			hs_result "$_st_name" busy '' '' '' 'another start is in progress'
			return
		fi
	fi

	hs_state "$_st_name" "$_st_port" "$_st_host"
	# A server stopped on purpose stays down until started by name; otherwise
	# the next login would undo every `stop`.
	if [ "$hs_state" = stopped ] && [ "$_st_resume" = 1 ]; then
		rm -f "$_st_dir/$_st_name.held"
		hs_state=down
	fi
	if [ "$hs_state" != down ]; then
		rmdir "$_st_lock"
		hs_report "$_st_name"
		return
	fi

	_st_script=$_st_dir/$_st_name.sh
	_st_log=$_st_dir/$_st_name.log
	{
		printf '#!/bin/sh\n# Written by handy servers on every start; edits here are lost.\n'
		# shellcheck disable=SC2016 # $HOME is for the generated script to expand
		printf 'SERVER=%s PORT=%s\nexport SERVER PORT\ncd "$HOME" || exit 1\n' \
			"$(hs_quote "$_st_host")" "$_st_port"
		printf '%s\n' "$_st_cmd"
	} >"$_st_script"
	printf '\n=== %s: starting (%s) ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$_st_mode" >>"$_st_log"
	_st_now=$(date +%s)

	case $_st_mode in
	tmux)
		if ! command -v tmux >/dev/null 2>&1; then
			rmdir "$_st_lock"
			hs_result "$_st_name" failed tmux '' '' 'tmux is not installed on this host'
			return
		fi
		# A session left over from a crash (held open for reading) is replaced.
		tmux kill-session -t "=handy-$_st_name" 2>/dev/null
		_st_pidtmp=$_st_dir/$_st_name.tmuxpid
		_st_wrapper=$_st_dir/$_st_name.tmux.sh
		rm -f "$_st_pidtmp"
		{
			printf '#!/bin/sh\n'
			# exec keeps the pid, so what lands in the file is the server's.
			# shellcheck disable=SC2016 # $$, $1, $2 belong to the inner sh
			printf 'sh -c %s _ %s %s\n' "$(hs_quote 'echo $$ >"$1"; exec sh "$2"')" \
				"$(hs_quote "$_st_pidtmp")" "$(hs_quote "$_st_script")"
			printf 'printf %s %s "$?"\n' "$(hs_quote '\n[handy: %s exited with status %s; press Enter to close]\n')" \
				"$(hs_quote "$_st_name")"
			printf 'read _\n'
		} >"$_st_wrapper"
		tmux new-session -d -s "handy-$_st_name" "sh $(hs_quote "$_st_wrapper")" </dev/null
		tmux pipe-pane -o -t "=handy-$_st_name:" "cat >> $(hs_quote "$_st_log")" </dev/null
		_st_i=0
		while [ ! -s "$_st_pidtmp" ] && [ "$_st_i" -lt 30 ]; do
			sleep 0.1
			_st_i=$((_st_i + 1))
		done
		read -r _st_pid <"$_st_pidtmp" 2>/dev/null
		_st_group=0
		;;
	*)
		# Own process group, so stop can take down everything the command spawned.
		if command -v setsid >/dev/null 2>&1; then
			setsid nohup sh "$_st_script" >>"$_st_log" 2>&1 </dev/null &
			_st_pid=$! _st_group=1
		elif command -v perl >/dev/null 2>&1; then
			nohup perl -e 'setpgrp(0, 0); exec @ARGV or die "exec: $!\n"' \
				sh "$_st_script" >>"$_st_log" 2>&1 </dev/null &
			_st_pid=$! _st_group=1
		else
			nohup sh "$_st_script" >>"$_st_log" 2>&1 </dev/null &
			_st_pid=$! _st_group=0
		fi
		;;
	esac

	printf '%s %s %s %s\n' "${_st_pid:-0}" "$_st_now" "$_st_mode" "$_st_group" >"$_st_dir/$_st_name.pid"
	rmdir "$_st_lock"

	sleep 1
	hs_read "$_st_dir/$_st_name.pid"
	if hs_alive; then
		hs_result "$_st_name" started "$_st_mode" "$_st_pid" 0 ''
	else
		_st_tail=$(tail -n 3 "$_st_log" | tr '\t\n' '  ')
		rm -f "$_st_dir/$_st_name.pid"
		hs_result "$_st_name" failed "$_st_mode" '' '' "exited at once: $_st_tail"
	fi
}

hs_stop() { # name host hold
	_sp_dir=$(hs_dir "$2")
	mkdir -p "$_sp_dir" 2>/dev/null
	if [ "$3" = 1 ]; then
		: >"$_sp_dir/$1.held"
	else
		rm -f "$_sp_dir/$1.held"
	fi
	hs_read "$_sp_dir/$1.pid"
	if [ "$hs_mode" = tmux ] && command -v tmux >/dev/null 2>&1; then
		tmux kill-session -t "=handy-$1" 2>/dev/null
	fi
	_sp_was=''
	if hs_alive; then
		_sp_was=$hs_pid
		hs_signal TERM
		_sp_i=0
		while hs_alive && [ "$_sp_i" -lt 20 ]; do
			sleep 0.5
			_sp_i=$((_sp_i + 1))
		done
		if hs_alive; then
			hs_signal KILL
			sleep 0.2
		fi
	fi
	_sp_detail=''
	if hs_alive; then
		hs_result "$1" failed "$hs_mode" "$hs_pid" '' 'still running after SIGKILL'
		return
	fi
	[ "$hs_group" = 0 ] && [ -n "$_sp_was" ] && [ "$hs_mode" != tmux ] &&
		_sp_detail='no setsid or perl here: only the top process and its children were signalled'
	rm -f "$_sp_dir/$1.pid" "$_sp_dir/$1.tmuxpid"
	if [ -n "$_sp_was" ]; then
		hs_result "$1" stopped '' "$_sp_was" '' "$_sp_detail"
	else
		hs_result "$1" down '' '' '' 'was not running'
	fi
}

hs_logs() { # name host lines follow
	_lg_file=$(hs_dir "$2")/$1.log
	if [ ! -f "$_lg_file" ]; then
		printf 'no log for %s yet\n' "$1" >&2
		return 1
	fi
	if [ "$4" = 1 ]; then
		exec tail -n "$3" -f "$_lg_file"
	fi
	tail -n "$3" "$_lg_file"
}

hs_http_ok() { # port
	if command -v curl >/dev/null 2>&1; then
		curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$1/" </dev/null 2>/dev/null
		_hp_rc=$?
	elif command -v python3 >/dev/null 2>&1; then
		python3 -c 'import sys, urllib.request; urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/", timeout=3)' \
			"$1" </dev/null 2>/dev/null
		_hp_rc=$?
	else
		hs_result _http unknown '' '' '' 'neither curl nor python3 is available'
		return
	fi
	if [ "$_hp_rc" -eq 0 ]; then
		hs_result _http ok '' '' '' ''
	else
		hs_result _http fail '' '' '' "no HTTP answer on port $1"
	fi
}

hs_forget() { # name host
	_fg_dir=$(hs_dir "$2")
	rm -f "$_fg_dir/$1.pid" "$_fg_dir/$1.log" "$_fg_dir/$1.sh" \
		"$_fg_dir/$1.tmux.sh" "$_fg_dir/$1.tmuxpid" "$_fg_dir/$1.held"
	rm -rf "$_fg_dir/$1.lock"
	rmdir "$_fg_dir" 2>/dev/null
	hs_result "$1" forgotten '' '' '' ''
}
