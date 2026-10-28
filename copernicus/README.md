# copernicus

includes UI and copernicus interface

Steps for running this 
1. python3 -m venv .venv && source .venv/bin/activate
2. cd to copernicus and pip install -r requirements.txt
3. copernicusmarine login   (login to a free account. The login stays on your machine, not in the repo)
4. python -m scripts.fetch_chl      (downloads the cache, takes a few minutes)
5. python -m avra.ui.app            (open http://127.0.0.1:8050)
