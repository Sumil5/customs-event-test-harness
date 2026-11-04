"""Builds realistic, valid declarations from a seed. Scenarios tweak what it builds."""

from __future__ import annotations

import random
import string
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from faker import Faker

from customs_harness.generator import reference_data as ref
from customs_harness.generator.model import Address, Declaration, GoodsItem, Party, Transport

MOVEMENTS = {
    # movement: (consignor region, consignee region)
    "GB_TO_NI": ("GB", "NI"),
    "NI_TO_GB": ("NI", "GB"),
    "EU_TO_NI": ("EU", "NI"),
    "NI_TO_EU": ("NI", "EU"),
}


class DeclarationFactory:
    """`seed` makes the business content reproducible. Identifiers (message ID,
    correlation ID, declaration reference) are always unique, even with a seed:
    re-using keys across runs would collide with rows already in Oracle and the
    idempotent pipeline would silently ignore the new messages."""

    def __init__(self, seed: int | None = None, now: datetime | None = None) -> None:
        self.rng = random.Random(seed)
        self._ids = random.SystemRandom()
        self.faker = Faker(list(dict.fromkeys(ref.FAKER_LOCALE.values())))
        self.faker.seed_instance(seed)
        self._now = now

    # ---------- identifiers ----------

    def uuid(self) -> str:
        return str(uuid.uuid4())

    def declaration_ref(self, country: str) -> str:
        alphabet = string.ascii_uppercase + string.digits
        year = (self._now or datetime.now(UTC)).strftime("%y")
        return year + country + "".join(self._ids.choices(alphabet, k=14))

    def eori(self, prefix: str) -> str:
        if prefix in ("GB", "XI"):
            return prefix + "".join(self.rng.choices(string.digits, k=12))
        return prefix + "".join(self.rng.choices(string.digits, k=10))

    # ---------- parties ----------

    def _street_lines(self, locale: str) -> tuple[str, str | None]:
        # Faker returns multi-line street addresses for some locales ("Flat 5\nMoss Green").
        lines = [part.strip() for part in self.faker[locale].street_address().split("\n") if part.strip()]
        return lines[-1][:70], (", ".join(lines[:-1])[:70] or None)

    def address(self, region: str, eu_country: str) -> Address:
        if region in ("GB", "NI"):
            line1, line2 = self._street_lines("en_GB")
            if region == "GB":
                town, postcode = self.rng.choice(ref.GB_TOWNS), self.faker["en_GB"].postcode()
            else:
                letters = "ABDEFGHJLNPQRSTUWXYZ"
                town = self.rng.choice(ref.NI_TOWNS)
                postcode = (
                    f"BT{self.rng.randint(1, 94)} {self.rng.randint(1, 9)}"
                    f"{self.rng.choice(letters)}{self.rng.choice(letters)}"
                )
            return Address(line1=line1, line2=line2, town=town, postcode=postcode, country_code="GB")
        line1, line2 = self._street_lines(ref.FAKER_LOCALE[eu_country])
        return Address(
            line1=line1,
            line2=line2,
            town=self.rng.choice(ref.EU_TOWNS[eu_country]),
            postcode=self.faker[ref.FAKER_LOCALE[eu_country]].postcode()[:10],
            country_code=eu_country,
        )

    def party(self, region: str, eu_country: str, with_eori: bool = True) -> Party:
        locale = "en_GB" if region in ("GB", "NI") else ref.FAKER_LOCALE[eu_country]
        prefix = {"GB": "GB", "NI": "XI"}.get(region, eu_country)
        return Party(
            name=self.faker[locale].company()[:70],
            address=self.address(region, eu_country),
            eori=self.eori(prefix) if with_eori else None,
        )

    # ---------- goods ----------

    def goods_item(self, number: int, controlled: bool = False, origin: str = "GB") -> GoodsItem:
        code, description = self.rng.choice(ref.CONTROLLED_COMMODITIES if controlled else ref.COMMODITIES)
        return GoodsItem(
            item_number=number,
            commodity_code=code,
            description=description,
            country_of_origin=origin,
            net_mass_kg=Decimal(self.rng.randint(500, 2_500_000)) / 1000,
            item_value=Decimal(self.rng.randint(1_000, 2_500_000)) / 100,
        )

    def vehicle_registration(self, region: str) -> str:
        letters = string.ascii_uppercase
        if region == "GB":
            return (
                f"{self.rng.choice(letters)}{self.rng.choice(letters)}{self.rng.randint(10, 75)} "
                + "".join(self.rng.choices(letters, k=3))
            )
        if region == "NI":
            prefix = f"{self.rng.choice(letters)}{self.rng.choice('IZ')}{self.rng.choice(letters)}"
            return f"{prefix} {self.rng.randint(1, 9999)}"
        return f"{self.rng.randint(15, 26)}{self.rng.choice(['1', '2'])}-D-{self.rng.randint(1, 99999)}"

    # ---------- whole declaration ----------

    def declaration(
        self,
        movement_type: str | None = None,
        item_count: int | None = None,
        controlled_goods: bool | None = None,
        ukims_authorised: bool | None = None,
        transport_mode: str | None = None,
    ) -> Declaration:
        now = self._now or datetime.now(UTC)
        movement = movement_type or self.rng.choice(list(MOVEMENTS))
        origin_region, destination_region = MOVEMENTS[movement]
        eu_country = self.rng.choice(list(ref.EU_TOWNS))
        consignor = self.party(origin_region, eu_country)
        consignee = self.party(
            destination_region, eu_country, with_eori=destination_region != "GB" or self.rng.random() < 0.7
        )

        mode = transport_mode or self.rng.choices(["ROAD", "SEA", "AIR", "RAIL"], weights=[70, 20, 8, 2])[0]
        port = None
        if mode in ("ROAD", "SEA") and not (movement in ("EU_TO_NI", "NI_TO_EU") and mode == "ROAD"):
            port = self.rng.choice(
                ref.GB_PORTS
                if destination_region == "GB"
                else ref.EU_PORTS
                if destination_region == "EU"
                else ref.NI_PORTS
            )
        transport = Transport(
            mode=mode,
            vehicle_registration=self.vehicle_registration(origin_region) if mode == "ROAD" else None,
            port_of_entry=port,
        )

        count = item_count or self.rng.choices([1, 2, 3, 4, 5, 8], weights=[45, 20, 15, 10, 7, 3])[0]
        if controlled_goods is None:
            controlled_goods = self.rng.random() < 0.15
        origin = consignor.address.country_code
        items = [
            self.goods_item(n, controlled=controlled_goods and n == 1, origin=origin)
            for n in range(1, count + 1)
        ]

        declaration = Declaration(
            message_id=self.uuid(),
            correlation_id=self.uuid(),
            created_at=now.replace(microsecond=(now.microsecond // 1000) * 1000),
            source_system=self.rng.choices(["TRADER_PORTAL", "API", "BULK_UPLOAD"], weights=[60, 35, 5])[0],
            declaration_ref=self.declaration_ref("XI" if destination_region == "NI" else "GB"),
            category=("IMPORT" if destination_region == "NI" else "EXPORT")
            if self.rng.random() < 0.9
            else "TRANSIT",
            movement_type=movement,
            declarant_eori=consignee.eori if consignee.eori and self.rng.random() < 0.6 else self.eori("GB"),
            consignor=consignor,
            consignee=consignee,
            transport=transport,
            arrival_date=(now + timedelta(days=self.rng.randint(0, 14))).date(),
            ukims_authorised=self.rng.random() < 0.75 if ukims_authorised is None else ukims_authorised,
            invoice_currency="EUR" if "EU" in (origin_region, destination_region) else "GBP",
            goods_items=items,
        )
        declaration.recalculate_totals(Decimal(self.rng.randint(102, 115)) / 100)
        return declaration
