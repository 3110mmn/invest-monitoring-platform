"""
ドメイン列挙型
"""
from enum import StrEnum


class InvestmentTargetType(StrEnum):
    INDIVIDUAL_STOCK = "individual_stock"
    ETF = "etf"
    MUTUAL_FUND = "mutual_fund"
    BOND = "bond"
