{
  description = "psi-agent - a microkernel-style agent framework";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    flake-parts.url = "github:hercules-ci/flake-parts";
  };

  outputs =
    inputs@{
      flake-parts,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      perSystem =
        { pkgs, lib, ... }:
        let
          python = pkgs.python314;

          # Version for hatch-vcs (git may be unavailable in the build sandbox).
          version = "0.0.1-alpha20260717";

          runtimeTools = with pkgs; [
            uv
            nodejs
            bashInteractive
            coreutils
            git
          ];

          # --- Vue SPA (gateway web UI) ---
          psi-agent-spa = pkgs.buildNpmPackage {
            pname = "psi-agent-spa";
            inherit version;
            src = ./src/psi_agent/gateway/spa;
            npmDepsHash = "sha256-3ETHeNgPMxEf6B+3ckgLM5i5Z5v7/AT36SSXIFds2SM=";
            # vite build → dist/
            installPhase = ''
              runHook preInstall
              mkdir -p "$out"
              cp -r dist/* "$out/"
              runHook postInstall
            '';
          };

          workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

          overlay = workspace.mkPyprojectOverlay {
            sourcePreference = "wheel";
          };

          pyprojectOverrides = final: prev: {
            # sdist-only deps
            proxy-tools = prev.proxy-tools.overrideAttrs (old: {
              nativeBuildInputs =
                (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem { setuptools = [ ]; };
            });

            psi-agent = prev.psi-agent.overrideAttrs (old: {
              # hatch-vcs reads the version from git; pin it for the sandbox.
              env = (old.env or { }) // {
                HATCH_VCS_PRETEND_VERSION = version;
              };
              # Inject the prebuilt SPA into the wheel's package data.
              postInstall = (old.postInstall or "") + ''
                spa_dist="$out/${python.sitePackages}/psi_agent/gateway/spa/dist"
                mkdir -p "$spa_dist"
                cp -r ${psi-agent-spa}/* "$spa_dist/"
              '';
            });
          };

          pythonSet =
            (pkgs.callPackage pyproject-nix.build.packages {
              inherit python;
            }).overrideScope
              (
                lib.composeManyExtensions [
                  pyproject-build-systems.overlays.default
                  overlay
                  pyprojectOverrides
                ]
              );

          venv = pythonSet.mkVirtualEnv "psi-agent-env" workspace.deps.default;

          psi-agent = pkgs.stdenv.mkDerivation {
            pname = "psi-agent";
            inherit version;
            nativeBuildInputs = [ pkgs.makeWrapper ];
            dontUnpack = true;
            installPhase = ''
              mkdir -p "$out/bin"
              makeWrapper ${venv}/bin/psi-agent "$out/bin/psi-agent" \
                --prefix PATH : ${lib.makeBinPath runtimeTools}
            '';
            meta = {
              description = "A microkernel-style agent framework";
              mainProgram = "psi-agent";
            };
          };
        in
        {
          packages = {
            default = psi-agent;
            inherit psi-agent psi-agent-spa;
            psi-agent-unwrapped = venv;
          };

          apps.default = {
            type = "app";
            program = "${psi-agent}/bin/psi-agent";
            meta.description = "A microkernel-style agent framework";
          };

          devShells.default = pkgs.mkShell {
            packages = [
              python
              pkgs.uv
              pkgs.nodejs
            ]
            ++ runtimeTools;
            env.UV_PYTHON = python.interpreter;
          };
        };
    };
}
