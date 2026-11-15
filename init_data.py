"""Explicit dataset reset: python init_data.py --reset (demo database only)."""
import argparse
from app.services.seed_service import SeedService
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true')
    args = parser.parse_args()
    print(SeedService.populate_seed_data(clear_existing=args.reset))
