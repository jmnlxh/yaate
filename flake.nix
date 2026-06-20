{
  description = "yaate — Yet Another AI-assisted Text Editor";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;

        yaate = python.pkgs.buildPythonApplication {
          pname = "yaate";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = [ python.pkgs.setuptools ];

          propagatedBuildInputs = with python.pkgs; [
            prompt-toolkit
            pygments
            rich
            python-dotenv
          ];
        };
      in
      {
        packages.default = yaate;

        # `nix develop` — drops you into a shell with all deps
        devShells.default = pkgs.mkShell {
          packages = [
            python
            python.pkgs.pip
            python.pkgs.prompt-toolkit
            python.pkgs.pygments
            python.pkgs.rich
            python.pkgs.python-dotenv

            # Local formatters (optional, used before Gemini fallback)
            pkgs.black          # python
            pkgs.alejandra      # nix
            pkgs.shfmt          # bash
            pkgs.nodePackages.prettier  # js/ts/html/css
          ];

          shellHook = ''
            # Install google-generativeai via pip into a local venv
            if [ ! -d .venv ]; then
              python -m venv .venv
              .venv/bin/pip install google-generativeai --quiet
            fi
            source .venv/bin/activate
            pip install -e . --quiet

            echo ""
            echo "  yaate dev shell ready"
            echo "  Add your GEMINI_API_KEY to .env"
            echo "  Run: yaate <file>"
            echo ""
          '';
        };
      });
}
