from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def _spec_output(self):
        return str(Path(self.build_lib) / "mcp_magichour" / "openapi.json")

    def run(self):
        super().run()
        if not getattr(self, "editable_mode", False):
            self.copy_file("docs/openapi.json", self._spec_output())

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        if self._spec_output() not in outputs:
            outputs.append(self._spec_output())
        return outputs

    def get_output_mapping(self):
        mapping = getattr(super(), "get_output_mapping", dict)()
        return {**mapping, self._spec_output(): "docs/openapi.json"}


setup(cmdclass={"build_py": BuildPy})
