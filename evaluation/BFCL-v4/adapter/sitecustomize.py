from bfcl_gateway import register

if register():
    from bfcl_gateway.search_provider import install

    install()
