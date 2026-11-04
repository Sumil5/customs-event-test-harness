-- ---------------------------------------------------------------------------
-- CUSTOMS schema: where the persistence service lands processed declarations.
--
-- gvenzl/oracle-free runs this once, on first start, as SYSDBA in the CDB root.
-- APP_USER (CUSTOMS) has already been created in FREEPDB1 by that point.
-- ---------------------------------------------------------------------------
WHENEVER SQLERROR EXIT SQL.SQLCODE

ALTER SESSION SET CONTAINER = FREEPDB1;
ALTER SESSION SET CURRENT_SCHEMA = CUSTOMS;

-- Header-level record: one row per accepted declaration.
CREATE TABLE declaration (
  declaration_ref        VARCHAR2(18)        NOT NULL,
  correlation_id         VARCHAR2(36)        NOT NULL,
  message_id             VARCHAR2(36)        NOT NULL,
  message_created_at     TIMESTAMP(3)        NOT NULL,   -- UTC
  source_system          VARCHAR2(20)        NOT NULL,
  declaration_category   VARCHAR2(2)         NOT NULL,   -- IM / EX / TR (code lookup)
  movement_type          VARCHAR2(10)        NOT NULL,
  declarant_eori         VARCHAR2(17)        NOT NULL,
  consignor_name         VARCHAR2(70 CHAR)   NOT NULL,
  consignor_country      VARCHAR2(2)         NOT NULL,
  consignee_name         VARCHAR2(70 CHAR)   NOT NULL,
  consignee_eori         VARCHAR2(17),
  consignee_town         VARCHAR2(35 CHAR)   NOT NULL,
  consignee_postcode     VARCHAR2(10),
  consignee_country      VARCHAR2(2)         NOT NULL,
  transport_mode_code    NUMBER(1)           NOT NULL,   -- 1 sea, 2 rail, 3 road, 4 air
  vehicle_registration   VARCHAR2(20),
  port_of_entry          VARCHAR2(5),
  arrival_date           DATE                NOT NULL,
  ukims_authorised       VARCHAR2(1)         NOT NULL,
  total_gross_mass_kg    NUMBER(14,3)        NOT NULL,
  invoice_currency       VARCHAR2(3)         NOT NULL,
  total_invoice_value    NUMBER(14,2)        NOT NULL,
  goods_item_count       NUMBER(3)           NOT NULL,
  risk_score             NUMBER(3)           NOT NULL,
  lane                   VARCHAR2(5)         NOT NULL,
  assessed_at            TIMESTAMP(3)        NOT NULL,   -- UTC
  persisted_at           TIMESTAMP(6)        DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP) NOT NULL,
  CONSTRAINT declaration_pk PRIMARY KEY (declaration_ref),
  CONSTRAINT declaration_ukims_ck CHECK (ukims_authorised IN ('Y', 'N')),
  CONSTRAINT declaration_lane_ck CHECK (lane IN ('GREEN', 'RED'))
);

CREATE INDEX declaration_correlation_ix ON declaration (correlation_id);
CREATE INDEX declaration_persisted_ix ON declaration (persisted_at);

-- Line-level records: one row per goods item on a declaration.
CREATE TABLE goods_item (
  declaration_ref     VARCHAR2(18)        NOT NULL,
  item_number         NUMBER(2)           NOT NULL,
  commodity_code      VARCHAR2(10)        NOT NULL,
  description         VARCHAR2(512 CHAR)  NOT NULL,
  country_of_origin   VARCHAR2(2)         NOT NULL,
  net_mass_kg         NUMBER(14,3)        NOT NULL,
  item_value          NUMBER(14,2)        NOT NULL,
  CONSTRAINT goods_item_pk PRIMARY KEY (declaration_ref, item_number),
  CONSTRAINT goods_item_declaration_fk FOREIGN KEY (declaration_ref)
    REFERENCES declaration (declaration_ref) ON DELETE CASCADE
);

COMMENT ON TABLE declaration IS 'Accepted goods movement declarations (one row per DeclarationRef)';
COMMENT ON TABLE goods_item IS 'Goods items belonging to a declaration';
COMMENT ON COLUMN declaration.persisted_at IS 'UTC time the row was committed; end of the traced journey';

EXIT
