# setup.py
from setuptools import find_packages, setup

setup(
    name="compras-emmanuelle",
    version="3.0.0",
    description="Sistema de compras, auditoría y pronóstico - Droguería Emmanuelle",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "pandas>=2.0.3",
        "numpy>=1.24.3",
        "openpyxl>=3.1.2",
        "PyYAML>=6.0",
        "pyodbc>=5.0.0",
        "scikit-learn>=1.3.0",
        "Flask>=3.0.0",
        "pyarrow>=15.0.0",
        "xlsxwriter>=3.1.0",
        "weasyprint>=62.0",
    ],
    python_requires=">=3.9",
)
