from setuptools import setup, find_packages
from pathlib import Path


this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name="htmlcmp",
    version="1.0.6",
    author="Andreas Stefl",
    author_email="stefl.andreas@gmail.com",
    description="Compare HTML files by rendered output",
    url="https://github.com/opendocument-app/compare-html",
    long_description=long_description,
    long_description_content_type="text/markdown",
    python_requires=">=3.7",
    packages=find_packages("src"),
    package_dir={"": "src"},
    install_requires=[
        "pillow",
        "selenium",
        "flask",
        "watchdog",
    ],
    extras_require={
        "dev": ["black"],
        "test": ["pytest>=6.0"],
    },
    entry_points = {
        "console_scripts": [
            "compare-html=htmlcmp.compare_output:main",
            "compare-html-server=htmlcmp.compare_output_server:main",
            "html-render-diff=htmlcmp.html_render_diff:main",
            "html-tidy=htmlcmp.tidy_output:main",
        ],
    },
)
