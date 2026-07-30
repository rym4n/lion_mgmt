"""Container/build smoke entrypoint for packaged topology contracts."""

from services.network_topology.contracts import smoke_all_contracts


def main() -> None:
    result = smoke_all_contracts()
    print(
        "network topology contracts loaded: "
        f"{result['schema_count']} schemas, {result['example_count']} examples, "
        f"{result['registry_count']} registries"
    )


if __name__ == "__main__":
    main()
