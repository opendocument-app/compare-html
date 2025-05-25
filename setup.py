from setuptools import setup, find_packages
from pathlib import Path


this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name="compare-html",
    version="1.0.0",
    author="Andreas Stefl",
    author_email="stefl.andreas@gmail.com",
    description="Compare HTML files by rendered output",
    url="https://github.com/opendocument-app/compare-html",
    long_description=long_description,
    long_description_content_type="text/markdown",
    python_requires=">=3.7",
    packages=find_packages("src"),
    package_dir={"": "src"},
    install_requires=[],
    extras_require={
        "dev": ["black"],
        "test": ["pytest>=6.0"],
    },
    entry_points = {
        "console_scripts": [
            "compare-html=compare-html.compare_output:main",
            "compare-html-server=compare-html.compare_output_server:main",
            "html-render-diff=compare-html.html_render_diff:main",
            "html-tidy=compare-html.tidy_output:main",
        ],
    },
)
