{ pkgs ? import <nixpkgs> {} }:

let
  version = "v8-nix";

in pkgs.dockerTools.buildLayeredImage {
  name = "predictive-agent";
  tag = version;

  config = {
    Cmd = [ "/entrypoint.sh" ];
    Envi = [
      "OPERATOR_VERSION=4.0.0"
      "LLM_BACKEND="
      "OLLAMA_URL="
      "OLLAMA_MODEL="
    ];
    ExposedPorts = {
      "8080/tcp" = {};
      "8081/tcp" = {};
    };
  };

  layers = [
    pkgs.dockerTools.runAsRoot ''
      apk add --no-cache \
        python3 \
        curl \
        bash \
        kubectl
      mkdir -p /predictive_agent /var/lib/opendesk
    ''

    (pkgs.dockerTools.copyToRoot {
      prefix = "predictive_agent";
      source = ../predictive_agent;
      filter = path: type: 
        type == "regular" || (type == "directory" && path == "/predictive_agent");
    })

    (pkgs.dockerTools.copyToRoot {
      source = ./predictive-agent-files;
    })

    pkgs.dockerTools.runAsRoot ''
      chmod +x /predictive-agent-files/entrypoint.sh /predictive-agent-files/healthcheck.sh
      ln -sf /predictive-agent-files/entrypoint.sh /entrypoint.sh
      ln -sf /predictive-agent-files/healthcheck.sh /healthcheck.sh
      chmod +x /entrypoint.sh /healthcheck.sh
    ''
  ];
}
