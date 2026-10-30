try:
    import networkx
    print("networkx", networkx.__version__)
except Exception as e:
    print("no networkx", e)
try:
    import pandas
    print("pandas ok", pandas.__version__)
except Exception as e:
    print("no pandas", e)
import sumolib
print("sumolib", sumolib.version if hasattr(sumolib, "version") else "loaded")