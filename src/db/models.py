from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from .engine import Base


def _utcnow() -> datetime:
    from src.utils.helpers import utcnow
    return utcnow()


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    owner = Column(Text, default="")
    label = Column(Text, unique=True, nullable=True)
    is_admin = Column(Boolean, default=False)
    prefix = Column(Text, nullable=False, unique=True)
    key_hash = Column(Text, nullable=False, unique=True)
    scopes = Column(ARRAY(Text), default=["data:read"])
    rpm = Column(Integer, default=60)
    enabled = Column(Boolean, default=True)
    revoked_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at < _utcnow()

    def to_public_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "owner": self.owner,
            "label": self.label,
            "is_admin": self.is_admin,
            "prefix": self.prefix,
            "scopes": self.scopes or [],
            "rpm": self.rpm,
            "enabled": self.enabled,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_dict(self) -> dict:
        return {c.key: getattr(self, c.key) for c in self.__table__.columns}


class PullJob(Base):
    __tablename__ = "pull_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Text, nullable=False, unique=True)
    symbol = Column(Text, nullable=False)
    country = Column(Text, default="in")
    source = Column(Text, default="nse")
    filing_type = Column(Text, nullable=True)
    refresh = Column(Boolean, default=False)
    status = Column(Text, default="queued")
    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    created_by = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    def to_public_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "symbol": self.symbol,
            "filing_type": self.filing_type,
            "refresh": self.refresh,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "result": self.result,
            "error": self.error,
        }


class NSEStockMetadata(Base):
    __tablename__ = "nse_stock_metadata"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, unique=True)
    source = Column(Text, default="NSE")
    last_pull = Column(DateTime, nullable=True)
    previous_pulls = Column(ARRAY(DateTime), default=[])
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow)


class APIKeyUsage(Base):
    __tablename__ = "api_key_usage"

    id = Column(Text, primary_key=True)
    count = Column(Integer, nullable=False, default=1)
    window_start = Column(BigInteger, nullable=False)


class IncomeStatement(Base):
    __tablename__ = "income_statements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False)
    period_end_date = Column(Date, nullable=False)
    period_start_date = Column(Date, nullable=True)
    xbrl_url = Column(Text, nullable=True)
    broadcast_date = Column(Text, nullable=True)
    consolidated = Column(Boolean, nullable=False)
    filing_type = Column(Text, nullable=False)
    measure = Column(Text, nullable=True)
    entity_identifier = Column(Text, nullable=True)
    fiscal_period = Column(Text, nullable=True)
    source_endpoint = Column(Text, nullable=False)
    context_ref_type = Column(Text, nullable=True)
    pulled_at = Column(DateTime, nullable=True)
    _content_hash = Column("_content_hash", Text, nullable=True)

    revenue_from_operations = Column(Numeric, nullable=True)
    other_income = Column(Numeric, nullable=True)
    income = Column(Numeric, nullable=True)
    finance_costs = Column(Numeric, nullable=True)
    depreciation_depletion_and_amortisation_expense = Column(Numeric, nullable=True)
    other_expenses = Column(Numeric, nullable=True)
    expenses = Column(Numeric, nullable=True)
    profit_before_exceptional_items_and_tax = Column(Numeric, nullable=True)
    exceptional_items_before_tax = Column(Numeric, nullable=True)
    profit_before_tax = Column(Numeric, nullable=True)
    current_tax = Column(Numeric, nullable=True)
    deferred_tax = Column(Numeric, nullable=True)
    tax_expense = Column(Numeric, nullable=True)
    profit_loss_for_period_from_continuing_operations = Column(Numeric, nullable=True)
    profit_loss_from_discontinued_operations_before_tax = Column(Numeric, nullable=True)
    tax_expense_of_discontinued_operations = Column(Numeric, nullable=True)
    profit_loss_from_discontinued_operations_after_tax = Column(Numeric, nullable=True)
    profit_loss_for_period = Column(Numeric, nullable=True)
    profit_or_loss_attributable_to_owners_of_parent = Column(Numeric, nullable=True)
    comprehensive_income_for_the_period = Column(Numeric, nullable=True)
    basic_earnings_loss_per_share_from_continuing_operations = Column(Numeric, nullable=True)
    diluted_earnings_loss_per_share_from_continuing_operations = Column(Numeric, nullable=True)
    basic_earnings_loss_per_share_from_discontinued_operations = Column(Numeric, nullable=True)
    diluted_earnings_loss_per_share_from_discontinued_operations = Column(Numeric, nullable=True)
    basic_earnings_loss_per_share_from_continuing_and_discontinued_operations = Column(Numeric, nullable=True)
    diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations = Column(Numeric, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "period_end_date", "consolidated", "source_endpoint",
                         name="uq_income_stmt"),
    )

    def to_dict(self) -> dict:
        result = {}
        for c in self.__table__.columns:
            val = getattr(self, c.key)
            if isinstance(val, datetime):
                val = val.isoformat()
            elif isinstance(val, date):
                val = val.isoformat()
            result[c.key] = val
        return result


class BalanceSheet(Base):
    __tablename__ = "balance_sheets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False)
    period_end_date = Column(Date, nullable=False)
    period_start_date = Column(Date, nullable=True)
    xbrl_url = Column(Text, nullable=True)
    broadcast_date = Column(Text, nullable=True)
    consolidated = Column(Boolean, nullable=False)
    filing_type = Column(Text, nullable=False)
    measure = Column(Text, nullable=True)
    entity_identifier = Column(Text, nullable=True)
    fiscal_period = Column(Text, nullable=True)
    source_endpoint = Column(Text, nullable=False)
    context_ref_type = Column(Text, nullable=True)
    pulled_at = Column(DateTime, nullable=True)
    _content_hash = Column("_content_hash", Text, nullable=True)

    paid_up_value_of_equity_share_capital = Column(Numeric, nullable=True)
    face_value_of_equity_share_capital = Column(Numeric, nullable=True)
    equity_share_capital = Column(Numeric, nullable=True)
    other_equity = Column(Numeric, nullable=True)
    debt_equity_ratio = Column(Numeric, nullable=True)
    noncurrent_liabilities = Column(Numeric, nullable=True)
    borrowings_current = Column(Numeric, nullable=True)
    borrowings_noncurrent = Column(Numeric, nullable=True)
    noncurrent_investments = Column(Numeric, nullable=True)
    trade_receivables_noncurrent = Column(Numeric, nullable=True)
    loans_noncurrent = Column(Numeric, nullable=True)
    other_noncurrent_financial_assets = Column(Numeric, nullable=True)
    noncurrent_financial_assets = Column(Numeric, nullable=True)
    deferred_tax_assets_net = Column(Numeric, nullable=True)
    other_noncurrent_assets = Column(Numeric, nullable=True)
    noncurrent_assets = Column(Numeric, nullable=True)
    capital_work_in_progress = Column(Numeric, nullable=True)
    investment_property = Column(Numeric, nullable=True)
    goodwill = Column(Numeric, nullable=True)
    other_intangible_assets = Column(Numeric, nullable=True)
    assets = Column(Numeric, nullable=True)
    cash_and_cash_equivalents = Column(Numeric, nullable=True)
    bank_balance_other_than_cash_and_cash_equivalents = Column(Numeric, nullable=True)
    reserve_excluding_revaluation_reserves = Column(Numeric, nullable=True)
    current_liabilities = Column(Numeric, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "period_end_date", "consolidated", "source_endpoint",
                         name="uq_balance_sheet"),
    )

    def to_dict(self) -> dict:
        result = {}
        for c in self.__table__.columns:
            val = getattr(self, c.key)
            if isinstance(val, datetime):
                val = val.isoformat()
            elif isinstance(val, date):
                val = val.isoformat()
            result[c.key] = val
        return result


class CashFlow(Base):
    __tablename__ = "cash_flows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False)
    period_end_date = Column(Date, nullable=False)
    period_start_date = Column(Date, nullable=True)
    xbrl_url = Column(Text, nullable=True)
    broadcast_date = Column(Text, nullable=True)
    consolidated = Column(Boolean, nullable=False)
    filing_type = Column(Text, nullable=False)
    measure = Column(Text, nullable=True)
    entity_identifier = Column(Text, nullable=True)
    fiscal_period = Column(Text, nullable=True)
    source_endpoint = Column(Text, nullable=False)
    context_ref_type = Column(Text, nullable=True)
    pulled_at = Column(DateTime, nullable=True)
    _content_hash = Column("_content_hash", Text, nullable=True)

    cash_flows_from_used_in_operations = Column(Numeric, nullable=True)
    cash_flows_from_used_in_operating_activities = Column(Numeric, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "period_end_date", "consolidated", "source_endpoint",
                         name="uq_cash_flow"),
    )

    def to_dict(self) -> dict:
        result = {}
        for c in self.__table__.columns:
            val = getattr(self, c.key)
            if isinstance(val, datetime):
                val = val.isoformat()
            elif isinstance(val, date):
                val = val.isoformat()
            result[c.key] = val
        return result


class Shareholding(Base):
    __tablename__ = "shareholdings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False)
    period_end_date = Column(Date, nullable=False)
    period_start_date = Column(Date, nullable=True)
    xbrl_url = Column(Text, nullable=True)
    broadcast_date = Column(Text, nullable=True)
    consolidated = Column(Boolean, nullable=False)
    filing_type = Column(Text, nullable=False)
    measure = Column(Text, nullable=True)
    entity_identifier = Column(Text, nullable=True)
    fiscal_period = Column(Text, nullable=True)
    source_endpoint = Column(Text, nullable=False)
    context_ref_type = Column(Text, nullable=True)
    pulled_at = Column(DateTime, nullable=True)
    _content_hash = Column("_content_hash", Text, nullable=True)

    promoters_and_promoter_group = Column(Numeric, nullable=True)
    foreign_institutional_investors = Column(Numeric, nullable=True)
    domestic_institutional_investors = Column(Numeric, nullable=True)
    non_institutions = Column(Numeric, nullable=True)
    public_shareholding = Column(Numeric, nullable=True)
    non_promoter_non_public_shareholding = Column(Numeric, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "period_end_date", "consolidated", "source_endpoint",
                         name="uq_shareholding"),
    )

    def to_dict(self) -> dict:
        result = {}
        for c in self.__table__.columns:
            val = getattr(self, c.key)
            if isinstance(val, datetime):
                val = val.isoformat()
            elif isinstance(val, date):
                val = val.isoformat()
            result[c.key] = val
        return result


class NSEAnnouncement(Base):
    __tablename__ = "nse_announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False)
    an_dt = Column(Text, nullable=True)
    attchmnt_text = Column(Text, nullable=True)
    desc = Column(Text, nullable=True)
    attchmnt_file = Column(Text, nullable=True)
    att_file_size = Column(BigInteger, nullable=True)
    has_xbrl = Column(Boolean, default=False)
    sort_date = Column(Text, nullable=True)
    raw_data = Column(JSONB, nullable=True)


class NSEAnnualReport(Base):
    __tablename__ = "nse_annual_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False)
    file_name = Column(Text, nullable=True)
    raw_data = Column(JSONB, nullable=True)
