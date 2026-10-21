"""Service module with unexpected missing import."""


def fetch_data():
    import uninstalled_vendor_module  # Trigger unmapped ModuleNotFoundError

    return uninstalled_vendor_module.fetch()
