import distutils.command.build
import distutils.log
import os
import pathlib
import subprocess
import sys
import tempfile
from sys import platform
from sys import version_info

from Cython.Build import cythonize
from setuptools import Extension
from setuptools import find_namespace_packages
from setuptools import setup
from setuptools.command.build_ext import build_ext as build_ext_orig

LIBBACKTRACE_LOCATION = (
    pathlib.Path(__file__).parent / "src" / "vendor" / "libbacktrace"
).resolve()

LIBBACKTRACE_INCLUDEDIRS = LIBBACKTRACE_LOCATION / "install" / "include"
LIBBACKTRACE_LIBDIR = LIBBACKTRACE_LOCATION / "install" / "lib"

ASSETS_LOCATION = (
    pathlib.Path(__file__).parent
    / "src"
    / "memray"
    / "reporters"
    / "templates"
    / "assets"
)


class BuildMemray(build_ext_orig):
    def run(self):
        self.build_js_files()
        self.build_libbacktrace()
        super().run()

    def announce_and_run(self, command, **kwargs):
        self.announce(
            "executing command: `{}`".format(" ".join(command)),
            level=distutils.log.INFO,
        )
        subprocess.run(command, check=True, **kwargs)

    def build_libbacktrace(self):

        archive_location = LIBBACKTRACE_LIBDIR / "libbacktrace.a"

        if archive_location.exists():
            return

        if not LIBBACKTRACE_LOCATION.exists():
            self.announce_and_run(
                [f"{LIBBACKTRACE_LOCATION.parent / 'regenerate_libbacktrace.sh'}"],
                cwd=LIBBACKTRACE_LOCATION.parent,
            )

        with tempfile.TemporaryDirectory() as tmpdirname:
            self.announce_and_run(
                [
                    f"{LIBBACKTRACE_LOCATION}/configure",
                    "--with-pic",
                    "--prefix",
                    f"{LIBBACKTRACE_LOCATION}/install",
                    "--includedir",
                    f"{LIBBACKTRACE_LOCATION}/install/include/libbacktrace",
                ],
                cwd=tmpdirname,
            )
            self.announce_and_run(["make", "-j"], cwd=tmpdirname)
            self.announce_and_run(["make", "install"], cwd=tmpdirname)

    def build_js_files(self):
        if any(ASSETS_LOCATION.glob("*.js")):
            return

        self.announce_and_run(["npm", "install"])
        self.announce_and_run(["npm", "run-script", "build"])


install_requires = [
    "jinja2",
    "typing_extensions; python_version < '3.8.0'",
    "rich < 11.0.0",
]
docs_requires = [
    "bump2version",
    "sphinx",
    "furo",
    "sphinx-argparse",
    "towncrier",
]

lint_requires = [
    "black",
    "flake8",
    "isort",
    "mypy",
    "check-manifest",
]

test_requires = [
    "Cython",
    "pytest",
    "pytest-cov",
]

benchmark_requires = [
    "asv",
]


TEST_BUILD = False
if "--test-build" in sys.argv:
    TEST_BUILD = True
    sys.argv.remove("--test-build")


if os.getenv("CYTHON_TEST_MACROS", None) is not None:
    TEST_BUILD = True

COMPILER_DIRECTIVES = {
    "language_level": 3,
    "embedsignature": True,
    "boundscheck": False,
    "wraparound": False,
    "cdivision": True,
    "profile": False,
    "linetrace": False,
    "c_string_type": "unicode",
    "c_string_encoding": "utf8",
}
EXTRA_COMPILE_ARGS = []
UNDEF_MACROS = []

if TEST_BUILD:
    COMPILER_DIRECTIVES = {
        "language_level": 3,
        "boundscheck": True,
        "embedsignature": True,
        "wraparound": True,
        "cdivision": False,
        "profile": False,
        "linetrace": False,
        "overflowcheck": True,
        "infer_types": True,
        "c_string_type": "unicode",
        "c_string_encoding": "utf8",
    }
    EXTRA_COMPILE_ARGS = ["-D_GLIBCXX_DEBUG", "-D_LIBCPP_DEBUG"]
    UNDEF_MACROS = ["NDEBUG"]

DEFINE_MACROS = []

# memray uses thread local storage (TLS) variables. As memray is compiled
# into a Python extension, is a shared object. TLS variables in shared objects
# use the most conservative and slow TLS model available by default:
# global-dynamic. This TLS model generates function calls (__tls_get_addr) to
# obtain the address of the TLS storage block, which is quite slow.  To
# circuvent the slowdown, memray uses by default a less restrictive model:
# initial-exec. This model is very fast but uses the limited TLS storage of the
# executable. This means that is possible that dlopen will refuse to load the
# shared object of the extension if there is not enough space. glibc reserves
# 1152 bytes for oportunustic usage for shared libraries with initial-exec, so
# this model will not present problems as long as the application uses glibc. In
# case these assumptions are wrong, memray can revert to use the most
# conservative model by setting the NO_MEMRAY_FAST_TLS environment variable.

MEMRAY_FAST_TLS = True
if os.getenv("NO_MEMRAY_FAST_TLS", None) is not None:
    MEMRAY_FAST_TLS = False

if MEMRAY_FAST_TLS:
    DEFINE_MACROS.append(("USE_MEMRAY_TLS_MODEL", "1"))

MEMRAY_EXTENSION = Extension(
    name="memray._memray",
    sources=[
        "src/memray/_memray.pyx",
        "src/memray/_memray/hooks.cpp",
        "src/memray/_memray/tracking_api.cpp",
        "src/memray/_memray/elf_shenanigans.cpp",
        "src/memray/_memray/logging.cpp",
        "src/memray/_memray/python_helpers.cpp",
        "src/memray/_memray/source.cpp",
        "src/memray/_memray/sink.cpp",
        "src/memray/_memray/records.cpp",
        "src/memray/_memray/record_reader.cpp",
        "src/memray/_memray/record_writer.cpp",
        "src/memray/_memray/snapshot.cpp",
        "src/memray/_memray/socket_reader_thread.cpp",
        "src/memray/_memray/native_resolver.cpp",
    ],
    libraries=["unwind"],
    library_dirs=[str(LIBBACKTRACE_LIBDIR)],
    include_dirs=["src", str(LIBBACKTRACE_INCLUDEDIRS)],
    language="c++",
    extra_compile_args=["-std=c++17", "-Wall", *EXTRA_COMPILE_ARGS],
    extra_link_args=["-std=c++17", "-l:libbacktrace.a"],
    define_macros=DEFINE_MACROS,
    undef_macros=UNDEF_MACROS,
)

MEMRAY_EXTENSION.libraries.append("dl")

if "linux" not in platform:
    raise RuntimeError("memray only supports Linux platforms")

about = {}
with open("src/memray/_version.py") as fp:
    exec(fp.read(), about)


HERE = pathlib.Path(__file__).parent.resolve()
LONG_DESCRIPTION = (HERE / "README.md").read_text(encoding="utf-8")

setup(
    name="memray",
    version=about["__version__"],
    python_requires=">=3.7.0",
    description="A memory profiler for Python applications",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    url="https://github.com/bloomberg/memray",
    author="Pablo Galindo Salgado",
    classifiers=[
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: Implementation :: CPython",
        "Topic :: Software Development :: Debuggers",
    ],
    license="Apache 2.0",
    package_dir={"": "src"},
    packages=find_namespace_packages(where="src"),
    ext_modules=cythonize(
        [MEMRAY_EXTENSION],
        include_path=["src/memray"],
        compiler_directives=COMPILER_DIRECTIVES,
    ),
    include_package_data=True,
    install_requires=install_requires,
    extras_require={
        "test": test_requires,
        "docs": docs_requires,
        "lint": lint_requires,
        "benchmark": benchmark_requires,
        "dev": test_requires + lint_requires + docs_requires + benchmark_requires,
    },
    entry_points={
        "console_scripts": [
            f"memray{version_info.major}.{version_info.minor}=memray.__main__:main",
            "memray=memray.__main__:main",
        ],
    },
    cmdclass={
        "build_ext": BuildMemray,
    },
    package_data={"memray": ["py.typed"]},
)
