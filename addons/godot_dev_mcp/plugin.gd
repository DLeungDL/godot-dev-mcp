@tool
extends EditorPlugin

const MAX_LOG_ENTRIES: int = 500
const DEFAULT_SNAPSHOT_LIMIT: int = 100
const MAX_SNAPSHOT_LIMIT: int = 500
const DEFAULT_TREE_DEPTH: int = 8
const MAX_TREE_DEPTH: int = 16
const MAX_SNAPSHOT_OFFSET: int = 100000
const MONITORS: Dictionary = {
	"fps": Performance.TIME_FPS,
	"process_ms": Performance.TIME_PROCESS,
	"physics_process_ms": Performance.TIME_PHYSICS_PROCESS,
	"object_count": Performance.OBJECT_COUNT,
	"node_count": Performance.OBJECT_NODE_COUNT,
	"resource_count": Performance.OBJECT_RESOURCE_COUNT,
	"orphan_node_count": Performance.OBJECT_ORPHAN_NODE_COUNT,
	"video_mem_used": Performance.RENDER_VIDEO_MEM_USED,
}

var _server: TCPServer = TCPServer.new()
var _clients: Array[StreamPeerTCP] = []
var _logs: Array[Dictionary] = []


func _enter_tree() -> void:
	var error: Error = _server.listen(7331, "127.0.0.1")
	if error != OK:
		_log("error", "godot-dev-mcp observer failed to listen on 127.0.0.1:7331")
		push_error("godot-dev-mcp observer failed to listen on 127.0.0.1:7331")
	else:
		_log("info", "godot-dev-mcp observer listening on 127.0.0.1:7331")
	set_process(true)


func _exit_tree() -> void:
	_server.stop()
	_clients.clear()


func _process(_delta: float) -> void:
	if _server.is_connection_available():
		var connection: StreamPeerTCP = _server.take_connection()
		if connection != null:
			_clients.append(connection)
	for client: StreamPeerTCP in _clients.duplicate():
		client.poll()
		if client.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_clients.erase(client)
			continue
		if client.get_available_bytes() > 0:
			var request: String = client.get_utf8_string(client.get_available_bytes())
			_respond(client, request)
			_clients.erase(client)


func _respond(client: StreamPeerTCP, request: String) -> void:
	var first_line: String = request.split("\r\n", false, 1)[0]
	var parts: PackedStringArray = first_line.split(" ")
	if parts.size() < 2 or parts[0] != "GET":
		_send_json(client, "405 Method Not Allowed", {"error": "read-only GET endpoints only"})
		return
	var target: String = parts[1]
	var path: String = target.get_slice("?", 0)
	var query: Dictionary = _parse_query(target.get_slice("?", 1) if target.contains("?") else "")
	match path:
		"/health":
			_send_json(client, "200 OK", {"ok": true, "service": "godot-dev-mcp-observer", "version": "0.2.0", "read_only": true})
		"/snapshot":
			_send_json(client, "200 OK", _snapshot(
				str(query.get("monitors", "")),
				_bounded_int(query, "offset", 0, 0, MAX_SNAPSHOT_OFFSET),
				_bounded_int(query, "limit", DEFAULT_SNAPSHOT_LIMIT, 1, MAX_SNAPSHOT_LIMIT),
				_bounded_int(query, "max_depth", DEFAULT_TREE_DEPTH, 0, MAX_TREE_DEPTH)
			))
		"/property":
			_send_json(client, "200 OK", _property(str(query.get("node", "")), str(query.get("property", ""))))
		"/logs":
			_send_json(client, "200 OK", {"entries": _logs.duplicate(true), "capacity": MAX_LOG_ENTRIES})
		"/screenshot":
			_send_screenshot(client)
		_:
			_send_json(client, "404 Not Found", {"error": "not found"})


func _parse_query(encoded: String) -> Dictionary:
	var result: Dictionary = {}
	for pair: String in encoded.split("&", false):
		var separator: int = pair.find("=")
		if separator >= 0:
			var key: String = pair.substr(0, separator).uri_decode()
			var value: String = pair.substr(separator + 1).uri_decode()
			result[key] = value
	return result


func _bounded_int(query: Dictionary, key: String, default_value: int, minimum: int, maximum: int) -> int:
	if not query.has(key) or not str(query[key]).is_valid_int():
		return default_value
	return clampi(int(query[key]), minimum, maximum)


func _send_json(client: StreamPeerTCP, status: String, payload: Dictionary) -> void:
	var body: String = JSON.stringify(payload)
	_send(client, status, "application/json; charset=utf-8", body.to_utf8_buffer())


func _send(client: StreamPeerTCP, status: String, content_type: String, body: PackedByteArray) -> void:
	var header: String = "HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n\r\n" % [status, content_type, body.size()]
	client.put_data(header.to_utf8_buffer())
	client.put_data(body)
	client.disconnect_from_host()


func _snapshot(requested_monitors: String, offset: int, limit: int, max_depth: int) -> Dictionary:
	var root: Window = get_tree().root
	var names: PackedStringArray = requested_monitors.split(",", false) if not requested_monitors.is_empty() else PackedStringArray(MONITORS.keys())
	var values: Dictionary = {}
	for monitor_name: String in names:
		if MONITORS.has(monitor_name):
			values[monitor_name] = Performance.get_monitor(MONITORS[monitor_name])
	var page: Dictionary = _node_page(root, offset, limit, max_depth)
	return {
		"schema_version": 2,
		"editor": Engine.is_editor_hint(),
		"read_only": true,
		"monitors": values,
		"root": {"name": root.name, "type": root.get_class(), "path": str(root.get_path())},
		"nodes": page["nodes"],
		"pagination": page["pagination"],
		"log_entries": _logs.size(),
	}


func _property(node_path: String, property_name: String) -> Dictionary:
	if node_path.is_empty() or property_name.is_empty() or not node_path.begins_with("/"):
		return {"ok": false, "error": "rooted node and property are required"}
	var node: Node = get_tree().root.get_node_or_null(NodePath(node_path.trim_prefix("/root/")))
	if node == null:
		return {"ok": false, "error": "node not found", "node": node_path}
	var allowed: bool = false
	for descriptor: Dictionary in node.get_property_list():
		if str(descriptor.get("name", "")) == property_name and int(descriptor.get("usage", 0)) & PROPERTY_USAGE_SCRIPT_VARIABLE == 0:
			allowed = true
			break
	if not allowed:
		return {"ok": false, "error": "property unavailable or script-defined"}
	return {"ok": true, "node": node_path, "property": property_name, "value": _safe_value(node.get(property_name))}


func _safe_value(value: Variant) -> Variant:
	if value == null or value is bool or value is int or value is float or value is String:
		return value
	if value is Vector2 or value is Vector3 or value is Color or value is Transform2D or value is Transform3D:
		return str(value)
	if value is Resource:
		return {"type": value.get_class(), "path": value.resource_path}
	if value is Node:
		return {"type": value.get_class(), "path": str(value.get_path())}
	return str(value)


func _send_screenshot(client: StreamPeerTCP) -> void:
	if DisplayServer.get_name() == "headless":
		_send_json(client, "503 Service Unavailable", {"error": "screenshot unavailable with the headless display server"})
		return
	var texture: ViewportTexture = get_tree().root.get_texture()
	if texture == null:
		_send_json(client, "503 Service Unavailable", {"error": "viewport texture unavailable in this renderer"})
		return
	var image: Image = texture.get_image()
	if image == null or image.is_empty():
		_send_json(client, "503 Service Unavailable", {"error": "viewport image unavailable"})
		return
	_send(client, "200 OK", "image/png", image.save_png_to_buffer())


func _node_page(root: Node, offset: int, limit: int, max_depth: int) -> Dictionary:
	var nodes: Array[Dictionary] = []
	var stack: Array[Dictionary] = [{"node": root, "depth": 0}]
	var seen: int = 0
	var has_more: bool = false
	while not stack.is_empty():
		var item: Dictionary = stack.pop_back()
		var node: Node = item["node"]
		var depth: int = item["depth"]
		if seen >= offset:
			if nodes.size() >= limit:
				has_more = true
				break
			nodes.append({
				"name": node.name,
				"type": node.get_class(),
				"path": str(node.get_path()),
				"depth": depth,
				"child_count": node.get_child_count(),
			})
		seen += 1
		if depth < max_depth:
			var children: Array[Node] = []
			for child: Node in node.get_children():
				children.append(child)
			for index: int in range(children.size() - 1, -1, -1):
				stack.append({"node": children[index], "depth": depth + 1})
	var next_offset: Variant = offset + nodes.size() if has_more else null
	return {
		"nodes": nodes,
		"pagination": {
			"offset": offset,
			"limit": limit,
			"returned": nodes.size(),
			"max_depth": max_depth,
			"has_more": has_more,
			"next_offset": next_offset,
		},
	}


func _log(level: String, message: String) -> void:
	_logs.append({"time_msec": Time.get_ticks_msec(), "level": level, "message": message})
	if _logs.size() > MAX_LOG_ENTRIES:
		_logs.pop_front()
