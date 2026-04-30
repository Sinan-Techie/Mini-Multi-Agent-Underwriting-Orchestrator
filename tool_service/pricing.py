"""Deterministic mock pricing engine.

Prices are deterministic so tests are repeatable.
Formula:
    price = BASE[region] + (age * AGE_FACTOR) + PROVIDER_OFFSET[provider]

Base rates differences:
    UAE: premium market
    KSA: slightly lower
    IND: significantly lower (purchasing power parity)
"""

BASE_RATE: dict[str, float] = {
    "UAE": 900.0,
    "KSA": 820.0,
    "IND": 380.0,
}

AGE_FACTOR: float = 6.5   # $ per year of age

PROVIDER_OFFSET: dict[str, float] = {
    "acme":    0.0,    # baseline
    "globex":  35.0,   # premium service, higher price
    "initech": -25.0,  # budget option, lower price
}

PROVIDERS = list(PROVIDER_OFFSET.keys())


def calculate_price(provider: str, age: int, region: str) -> float:
    """
    Return a deterministic annual premium in USD.
    Raises ValueError for unknown provider or region.
    """
    if provider not in PROVIDER_OFFSET:
        raise ValueError(f"unknown provider: {provider!r}. Valid: {PROVIDERS}")
    if region not in BASE_RATE:
        raise ValueError(f"unknown region: {region!r}. Valid: {list(BASE_RATE)}")
    if not (18 <= age <= 75):
        raise ValueError(f"age {age} out of insurable range (18–75)")

    price = BASE_RATE[region] + (age * AGE_FACTOR) + PROVIDER_OFFSET[provider]
    return round(price, 2)