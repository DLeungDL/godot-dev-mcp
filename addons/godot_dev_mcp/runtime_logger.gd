extends Logger

const MAX_PENDING_ENTRIES: int = 1000

var _mutex: Mutex = Mutex.new()
var _pending: Array[Dictionary] = []
var _dropped_entries: int = 0


func _log_message(message: String, error: bool) -> void:
	_enqueue({
		"time_msec": Time.get_ticks_msec(),
		"level": "error" if error else "info",
		"message": message,
		"source": "engine",
		"kind": "message",
	})


func _log_error(
	function: String,
	file: String,
	line: int,
	code: String,
	rationale: String,
	editor_notify: bool,
	error_type: int,
	script_backtraces: Array[ScriptBacktrace]
) -> void:
	_enqueue({
		"time_msec": Time.get_ticks_msec(),
		"level": "warning" if error_type == Logger.ERROR_TYPE_WARNING else "error",
		"message": rationale if not rationale.is_empty() else code,
		"source": "engine",
		"kind": "error",
		"error_type": error_type,
		"function": function,
		"file": file,
		"line": line,
		"code": code,
		"editor_notify": editor_notify,
		"backtrace_count": script_backtraces.size(),
	})


func drain() -> Dictionary:
	_mutex.lock()
	var entries: Array[Dictionary] = _pending.duplicate(true)
	var dropped: int = _dropped_entries
	_pending.clear()
	_dropped_entries = 0
	_mutex.unlock()
	return {"entries": entries, "dropped": dropped}


func _enqueue(entry: Dictionary) -> void:
	_mutex.lock()
	if _pending.size() >= MAX_PENDING_ENTRIES:
		_pending.pop_front()
		_dropped_entries += 1
	_pending.append(entry)
	_mutex.unlock()
