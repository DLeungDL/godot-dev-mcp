@tool
extends EditorPlugin

var _server := TCPServer.new()
var _clients: Array[StreamPeerTCP] = []


func _enter_tree() -> void:
	var error := _server.listen(7331, "127.0.0.1")
	if error != OK:
		push_error("Grand Sire observer failed to listen on 127.0.0.1:7331")
	set_process(true)


func _exit_tree() -> void:
	_server.stop()
	_clients.clear()


func _process(_delta: float) -> void:
	if _server.is_connection_available():
		_clients.append(_server.take_connection())
	for client in _clients.duplicate():
		client.poll()
		if client.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_clients.erase(client)
			continue
		if client.get_available_bytes() > 0:
			var request := client.get_utf8_string(client.get_available_bytes())
			_respond(client, request)
			_clients.erase(client)


func _respond(client: StreamPeerTCP, request: String) -> void:
	var path := request.split(" ")[1] if request.contains(" ") else "/"
	var status := "200 OK"
	var payload: Dictionary
	if path == "/snapshot":
		payload = _snapshot()
	elif path == "/health":
		payload = {"ok": true, "service": "grand-sire-observer"}
	else:
		status = "404 Not Found"
		payload = {"error": "not found"}
	var body := JSON.stringify(payload)
	var response := "HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % [status, body.to_utf8_buffer().size(), body]
	client.put_data(response.to_utf8_buffer())
	client.disconnect_from_host()


func _snapshot() -> Dictionary:
	var root := get_tree().root
	return {
		"editor": Engine.is_editor_hint(),
		"fps": Engine.get_frames_per_second(),
		"object_count": Performance.get_monitor(Performance.OBJECT_COUNT),
		"node_count": Performance.get_monitor(Performance.OBJECT_NODE_COUNT),
		"resource_count": Performance.get_monitor(Performance.OBJECT_RESOURCE_COUNT),
		"tree": _node_summary(root, 0),
	}


func _node_summary(node: Node, depth: int) -> Dictionary:
	var result := {"name": node.name, "type": node.get_class(), "children": []}
	if depth >= 6:
		result["truncated"] = node.get_child_count() > 0
		return result
	for child in node.get_children():
		result["children"].append(_node_summary(child, depth + 1))
	return result

