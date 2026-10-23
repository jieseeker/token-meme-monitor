#!/usr/bin/env ruby
# Convert the local Clash subscription into localhost-only HTTP endpoints.
require "json"
require "yaml"

abort "usage: generate-sing-box.rb SOURCE_YAML OUTPUT_JSON" unless ARGV.length == 2
nodes = YAML.safe_load(File.read(ARGV[0]), permitted_classes: [], permitted_symbols: [], aliases: false).fetch("proxies")
abort "proxy list is empty" unless nodes.is_a?(Array) && !nodes.empty?
abort "too many proxies for local port range" if nodes.length > 1000

config = {
  "log" => { "level" => "warn" },
  "inbounds" => [],
  "outbounds" => [{ "type" => "direct", "tag" => "direct" }],
  "route" => { "rules" => [], "final" => "direct" }
}

nodes.each_with_index do |node, index|
  abort "invalid proxy at index #{index}" unless node.is_a?(Hash)
  type = node.fetch("type")
  port = Integer(node.fetch("port"))
  abort "invalid port at index #{index}" unless (1..65_535).cover?(port)
  tag = "node-#{index + 1}"
  outbound = {
    "type" => type,
    "tag" => tag,
    "server" => node.fetch("server"),
    "server_port" => port
  }
  case type
  when "vless"
    reality = node.fetch("reality-opts")
    outbound["uuid"] = node.fetch("uuid")
    outbound["flow"] = node.fetch("flow")
    outbound["tls"] = {
      "enabled" => true,
      "server_name" => node.fetch("servername"),
      "insecure" => node.fetch("skip-cert-verify", false),
      "utls" => { "enabled" => true, "fingerprint" => node.fetch("client-fingerprint", "chrome") },
      "reality" => {
        "enabled" => true,
        "public_key" => reality.fetch("public-key"),
        "short_id" => reality.fetch("short-id")
      }
    }
  when "hysteria2"
    outbound["password"] = node.fetch("password")
    outbound["tls"] = {
      "enabled" => true,
      "server_name" => node.fetch("sni"),
      "insecure" => node.fetch("skip-cert-verify", false)
    }
  else
    abort "unsupported proxy type at index #{index}: #{type}"
  end
  inbound = "http-#{index + 1}"
  config["outbounds"] << outbound
  config["inbounds"] << {
    "type" => "http", "tag" => inbound,
    "listen" => "127.0.0.1", "listen_port" => 19_000 + index
  }
  config["route"]["rules"] << {
    "inbound" => [inbound], "action" => "route", "outbound" => tag
  }
end

File.open(ARGV[1], File::WRONLY | File::CREAT | File::TRUNC, 0o600) do |file|
  file.write(JSON.generate(config))
end
File.chmod(0o600, ARGV[1])
puts "Prepared #{nodes.length} local proxy endpoints"
