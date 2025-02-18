#!/usr/bin/env python3
import glob
from pathlib import Path
import requests
from mutagen.flac import FLAC
from tqdm import tqdm
import difflib
import argparse
import sys


class FlacLyricsUpdater:
    """Class to update FLAC files with synchronized lyrics.

    Attributes:
        base_dir (str): The base directory to search for FLAC files.
        force (bool): If True, always update the lyrics even if they already exist.
        only_local (bool): If True, disable online retrieval (use only local .lrc files).
        only_online (bool): If True, disable local .lrc search and always fetch lyrics from the API.
        lrc_folder (str): The folder from which to search for .lrc files.
        skipped_count (int): Number of FLAC files skipped (existing lyrics when not forcing).
        not_found_count (int): Number of files for which lyrics could not be obtained.
        modified_count (int): Number of files updated with new lyrics.
        api_call_count (int): Number of API calls made to lrclib.net.
        lrc_map (dict[Path, list[tuple[Path, str]]]): Mapping of directories to available .lrc files.
        session (requests.Session): Session for making HTTP requests.
    """

    def __init__(
        self,
        base_dir: str = ".",
        force: bool = False,
        only_local: bool = False,
        only_online: bool = False,
        local_folder: str | None = None,
    ) -> None:
        """Initializes the updater with the given options.

        Args:
            base_dir (str): Working directory to search for FLAC files. Defaults to the current directory.
            force (bool): If True, always update the lyrics tag, ignoring existing content.
                          Defaults to False.
            only_local (bool): If True, disable online retrieval of lyrics.
                               Defaults to False.
            only_online (bool): If True, disable local .lrc search and always fetch lyrics from the API.
                                Defaults to False.
            local_folder (str | None): Folder from which to search for local .lrc files.
                                       Defaults to None (which means use base_dir).
        """
        self.base_dir: str = base_dir
        self.force: bool = force
        self.only_local: bool = only_local
        self.only_online: bool = only_online
        # Use provided local_folder or default to base_dir.
        self.lrc_folder: str = local_folder if local_folder is not None else base_dir

        self.skipped_count: int = 0
        self.not_found_count: int = 0
        self.modified_count: int = 0
        self.api_call_count: int = 0

        # Pre-build a cache of local .lrc files from the specified lrc_folder.
        self.lrc_map: dict[Path, list[tuple[Path, str]]] = self.build_lrc_map()
        # Use a session to reuse HTTP connections.
        self.session: requests.Session = requests.Session()

    def build_lrc_map(self) -> dict[Path, list[tuple[Path, str]]]:
        """Builds a mapping of local .lrc files.

        Recursively finds all .lrc files in lrc_folder and builds a dictionary where the
        key is the absolute path of a directory, and the value is a list of tuples
        (Path to the file, its stem in lowercase).

        Returns:
            dict[Path, list[tuple[Path, str]]]: Mapping of directories to list of (.lrc file Path, lowercase stem) tuples.
        """
        lrc_map: dict[Path, list[tuple[Path, str]]] = {}
        lrc_files: list[str] = glob.glob(f"{self.lrc_folder}/**/*.lrc", recursive=True)
        for lrc in lrc_files:
            lrc_path: Path = Path(lrc)
            directory: Path = lrc_path.parent.resolve()
            stem: str = lrc_path.stem.lower()
            lrc_map.setdefault(directory, []).append((lrc_path, stem))
        return lrc_map

    def fetch_lyrics_from_api(
        self,
        title: str,
        artist: str,
        album: str | None = None,
        duration: float | None = None,
    ) -> str | None:
        """Fetches synchronized lyrics from the lrclib.net API.

        Args:
            title (str): The title of the track.
            artist (str): The artist name.
            album (str | None): The album name (optional).
            duration (float | None): The track's duration in seconds (optional).

        Returns:
            str | None: The lyrics text if found, otherwise None.
        """
        self.api_call_count += 1
        params: dict[str, str] = {
            "track_name": title,
            "artist_name": artist,
        }
        if album is not None:
            params["album_name"] = album
        if duration is not None:
            params["duration"] = str(duration)
        url: str = "https://lrclib.net/api/search"  # Example URL; update if needed.
        try:
            response: requests.Response = self.session.get(
                url, params=params, timeout=10
            )
            if response.status_code == 200:
                data: list[dict[str, str]] | None = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    synced_lyrics: str | None = data[0].get("syncedLyrics")
                    if synced_lyrics:
                        return synced_lyrics
            return None
        except Exception as e:
            print(f"Error fetching lyrics from API for '{title}' by '{artist}': {e}")
            return None

    def find_local_lrc(
        self, directory: Path, artist: str, title: str, threshold: float = 0.6
    ) -> Path | None:
        """Searches for a local .lrc file using fuzzy matching.

        Searches in the specified directory for a .lrc file by comparing the candidate
        string "artist - title" (in lowercase) with the file stems in that directory.

        Args:
            directory (Path): Directory in which to search for .lrc files.
            artist (str): The artist name.
            title (str): The track title.
            threshold (float): Minimum similarity ratio to consider a match. Defaults to 0.6.

        Returns:
            Path | None: The best matching .lrc file path if similarity score is at or above
                         the threshold; otherwise, None.
        """
        candidate: str = f"{artist} - {title}".lower()
        best_match: Path | None = None
        best_score: float = 0.0
        for lrc_path, lrc_stem in self.lrc_map.get(directory, []):
            score: float = difflib.SequenceMatcher(None, candidate, lrc_stem).ratio()
            if score > best_score:
                best_score = score
                best_match = lrc_path
        if best_score >= threshold:
            return best_match
        return None

    def process_flac_file(self, flac_file_path: str) -> None:
        """Processes a single FLAC file.

        The method extracts metadata from the FLAC file and, depending on CLI flags:
          - If --force is not set and the file already has lyrics, it is skipped.
          - If --only-online is enabled, the local search is skipped.
          - Otherwise, it attempts a fuzzy search for a local .lrc file.
          - If --only-local is enabled, online retrieval is disabled.
          - If neither mode is forced, local search is attempted first; if no lyrics are found then the API is called.
          - Finally, if lyrics are obtained, they are embedded into the FLAC file.

        Args:
            flac_file_path (str): Path to the FLAC file.
        """
        try:
            audio: FLAC = FLAC(flac_file_path)
        except Exception as e:
            print(f"Error opening FLAC file {flac_file_path}: {e}")
            return

        # Skip updating if lyrics exist and force is not enabled.
        if not self.force and "LYRICS" in audio and audio["LYRICS"]:
            self.skipped_count += 1
            return

        title: str | None = audio.get("TITLE", [None])[0]
        artist: str | None = audio.get("ARTIST", [None])[0]
        if not title or not artist:
            print(
                f"Missing TITLE or ARTIST metadata in file {flac_file_path}. Cannot fetch lyrics."
            )
            self.not_found_count += 1
            return

        album: str | None = audio.get("ALBUM", [None])[0]
        duration: float | None = getattr(audio.info, "length", None)

        flac_directory: Path = Path(flac_file_path).parent.resolve()

        # If --only-online flag is set, skip local search.
        local_lrc: Path | None = None
        if not self.only_online:
            local_lrc = self.find_local_lrc(flac_directory, artist, title)

        lyrics_text: str | None = None
        if local_lrc:
            try:
                with open(local_lrc, "r", encoding="utf-8") as f:
                    lyrics_text = f.read().strip()
            except Exception as e:
                print(f"Error reading local LRC file {local_lrc}: {e}")

        # If no lyrics found locally, attempt API retrieval unless --only-local is set.
        if not lyrics_text and not self.only_local:
            lyrics_text = self.fetch_lyrics_from_api(title, artist, album, duration)

        if not lyrics_text:
            self.not_found_count += 1
            print(f"No lyrics found for {flac_file_path}")
            return

        try:
            audio["LYRICS"] = [lyrics_text]
            audio.save()
            self.modified_count += 1
            print(f"Lyrics added to: {flac_file_path}")
        except Exception as e:
            print(f"Error saving lyrics to FLAC file {flac_file_path}: {e}")

    def run(self) -> None:
        """Recursively finds FLAC files, processes each file, and prints summary statistics."""
        flac_files: list[str] = glob.glob(f"{self.base_dir}/**/*.flac", recursive=True)
        print(f"Found {len(flac_files)} FLAC files.")
        for flac_file in tqdm(flac_files, desc="Processing FLAC files", unit="file"):
            self.process_flac_file(flac_file)
        self.print_summary()

    def print_summary(self) -> None:
        """Prints a summary of the processing results."""
        print("\n--- Summary ---")
        print(f"Files skipped (already had lyrics): {self.skipped_count}")
        print(f"Files with no lyrics found: {self.not_found_count}")
        print(f"Files updated with new lyrics: {self.modified_count}")
        print(f"API calls made to fetch lyrics: {self.api_call_count}")


def main() -> None:
    """Parses command-line arguments and runs the FLAC lyrics updater."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="FLAC Lyrics Updater. Recursively searches for FLAC files and embeds synchronized lyrics."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Working directory for FLAC files (default: current directory)",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force update: overwrite existing LYRICS tag even if present.",
    )
    parser.add_argument(
        "--only-local",
        action="store_true",
        help="Disable online retrieval of lyrics; use only local .lrc files.",
    )
    parser.add_argument(
        "--only-online",
        action="store_true",
        help="Disable local .lrc search and always fetch lyrics from the API.",
    )
    parser.add_argument(
        "--local-folder",
        type=str,
        default=None,
        help="Folder from which to search for local .lrc files (default: same as working directory)",
    )
    args: argparse.Namespace = parser.parse_args()

    # Check for contradictory options: --only-local and --only-online together.
    if args.only_local and args.only_online:
        print(
            "Ah, the paradox of choice! Specifying both --only-local and --only-online is like trying to be in two worlds at once. Please choose one, and let the universe remain in balance."
        )
        sys.exit(1)

    updater: FlacLyricsUpdater = FlacLyricsUpdater(
        base_dir=args.directory,
        force=args.force,
        only_local=args.only_local,
        only_online=args.only_online,
        local_folder=args.local_folder,
    )
    updater.run()


if __name__ == "__main__":
    main()
