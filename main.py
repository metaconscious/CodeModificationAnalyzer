import git
import os
import sys
import argparse
import tempfile
import shutil
import re
import urllib.parse
from datetime import datetime
from tqdm import tqdm


def validate_git_url(url):
    """Check if the URL is a valid Git repository URL."""
    # Common Git hosting domains
    git_domains = [
        'github.com', 'gitlab.com', 'bitbucket.org', 'dev.azure.com',
        'gitlab.', 'github.', 'bitbucket.', 'azure.'
    ]

    parsed_url = urllib.parse.urlparse(url)

    # Check if it's an HTTP/HTTPS URL with a valid Git domain
    if parsed_url.scheme in ['http', 'https']:
        for domain in git_domains:
            if domain in parsed_url.netloc:
                return True

    # Check if it's an SSH URL (git@github.com:user/repo.git)
    if parsed_url.scheme == 'git' or '@' in url and ':' in url and url.endswith('.git'):
        return True

    return False


def clone_repository(url, auth_token=None, username=None, password=None):
    """Clone a remote repository to a temporary directory."""
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp(prefix="git_analysis_")

    try:
        # Prepare authenticated URL if credentials provided
        clone_url = url
        if auth_token and 'github.com' in url:
            # Format: https://{token}@github.com/username/repo.git
            clone_url = url.replace('https://', f'https://{auth_token}@')
        elif username and password:
            # Format: https://{username}:{password}@domain.com/path
            parsed = urllib.parse.urlparse(url)
            netloc = f"{username}:{password}@{parsed.netloc}"
            clone_url = url.replace(parsed.netloc, netloc)

        print(f"Cloning repository: {url}")
        git.Repo.clone_from(clone_url, temp_dir)
        return temp_dir

    except git.exc.GitCommandError as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise Exception(f"Failed to clone repository: {str(e)}")


def get_author_modifications(repo_path, author_name, branch='main',
                             start_date=None, end_date=None, file_paths=None,
                             is_cloned=False):
    """
    Calculate total lines of code modified by a specific author.

    Args:
        repo_path (str): Path to the Git repository or URL
        author_name (str): Name or regex pattern of the author to filter by
        branch (str): Branch to analyze (default: main)
        start_date (datetime, optional): Start date for filtering commits
        end_date (datetime, optional): End date for filtering commits
        file_paths (list, optional): List of file paths to filter by
        is_cloned (bool): Whether the repository is a temporary clone

    Returns:
        dict: Statistics about the author's modifications
    """
    temp_dir = None

    try:
        # Handle repository URL
        if validate_git_url(repo_path):
            print(f"Detecting URL: {repo_path}")
            temp_dir = clone_repository(repo_path)
            repo_path = temp_dir
            is_cloned = True

        # Open the repository
        try:
            repo = git.Repo(repo_path)
        except git.exc.InvalidGitRepositoryError:
            return {"error": f"Invalid Git repository: {repo_path}"}
        except Exception as e:
            return {"error": f"Error opening repository: {str(e)}"}

        # Prepare author pattern matching (support regex)
        author_pattern = re.compile(author_name, re.IGNORECASE)

        # Get available branches
        available_branches = [b.name for b in repo.branches]

        # Check if branch exists, try alternatives if not
        if branch not in available_branches:
            # Try common main branch names
            for alt_branch in ['main', 'master', 'develop', 'dev']:
                if alt_branch in available_branches:
                    branch = alt_branch
                    print(f"Branch '{branch}' not found, using '{alt_branch}' instead")
                    break
            else:
                # If no common branch found, use the first available branch
                if available_branches:
                    branch = available_branches[0]
                    print(f"Branch '{branch}' not found, using '{available_branches[0]}' instead")
                else:
                    return {"error": "No branches found in repository"}

        # Initialize statistics
        commits_by_author = []
        total_additions = 0
        total_deletions = 0
        file_stats = {}

        # Count total commits for progress bar
        total_commits = sum(1 for _ in repo.iter_commits(branch))

        # Iterate through all commits in the branch with progress bar
        for commit in tqdm(repo.iter_commits(branch), total=total_commits, desc="Processing commits"):
            # Convert commit timestamp to datetime
            commit_date = datetime.fromtimestamp(commit.committed_date)

            # Check if commit is within date range (if specified)
            date_in_range = True
            if start_date and commit_date < start_date:
                date_in_range = False
            if end_date and commit_date > end_date:
                date_in_range = False

            # Filter by author (using regex) and date
            if author_pattern.search(commit.author.name) and date_in_range:
                commits_by_author.append(commit)

                # Process file changes
                if file_paths:
                    # Only process specified files
                    for file_path in file_paths:
                        # Check for wildcard paths
                        is_wildcard = '*' in file_path

                        for changed_file, file_change in commit.stats.files.items():
                            # Check if file matches the filter
                            if (is_wildcard and file_path.replace('*', '') in changed_file) or \
                                    (not is_wildcard and file_path == changed_file):
                                total_additions += file_change['insertions']
                                total_deletions += file_change['deletions']

                                # Track per-file statistics
                                if changed_file not in file_stats:
                                    file_stats[changed_file] = {
                                        'insertions': 0,
                                        'deletions': 0
                                    }
                                file_stats[changed_file]['insertions'] += file_change['insertions']
                                file_stats[changed_file]['deletions'] += file_change['deletions']
                else:
                    # Get overall stats for this commit
                    stats = commit.stats.total
                    total_additions += stats['insertions']
                    total_deletions += stats['deletions']

                    # Track per-file statistics
                    for changed_file, file_change in commit.stats.files.items():
                        if changed_file not in file_stats:
                            file_stats[changed_file] = {
                                'insertions': 0,
                                'deletions': 0
                            }
                        file_stats[changed_file]['insertions'] += file_change['insertions']
                        file_stats[changed_file]['deletions'] += file_change['deletions']

        result = {
            'author': author_name,
            'total_commits': len(commits_by_author),
            'lines_added': total_additions,
            'lines_deleted': total_deletions,
            'total_lines_modified': total_additions + total_deletions,
            'file_stats': file_stats,
            'first_commit_date': commits_by_author[-1].committed_datetime.strftime(
                '%Y-%m-%d') if commits_by_author else None,
            'last_commit_date': commits_by_author[0].committed_datetime.strftime(
                '%Y-%m-%d') if commits_by_author else None,
            'repository': repo_path if not is_cloned else repo.remotes.origin.url
        }

        return result

    finally:
        # Clean up temporary directory if we created one
        if temp_dir and is_cloned:
            try:
                shutil.rmtree(temp_dir)
                print(f"Cleaned up temporary repository at {temp_dir}")
            except Exception as e:
                print(f"Warning: Failed to clean up temporary directory: {str(e)}")


def parse_date(date_str):
    """Parse date from string in format YYYY-MM-DD"""
    if not date_str:
        return None

    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        print(f"Invalid date format: {date_str}. Please use YYYY-MM-DD.")
        return None


def interactive_mode():
    """Run the script in interactive mode"""
    print("=== Git Repository Code Modification Analyzer ===")

    # Get repository path or URL
    repo_input = input("Enter path to Git repository, URL, or '.' for current directory: ").strip()
    if not repo_input:
        repo_input = '.'

    # Check if it's a URL
    is_url = validate_git_url(repo_input)

    # If not URL, validate local path
    if not is_url and not os.path.exists(repo_input):
        print(f"Error: Path {repo_input} does not exist.")
        return

    # Authentication for remote repositories
    auth_token = None
    username = None
    password = None

    if is_url:
        print("\nRepository authentication (press Enter to skip if public):")
        auth_choice = input("Authentication type: [1] Access Token, [2] Username/Password, [3] None: ").strip()

        if auth_choice == '1':
            auth_token = input("Enter access token: ").strip()
        elif auth_choice == '2':
            username = input("Enter username: ").strip()
            password = input("Enter password: ").strip()

    # Get branch
    branch = input("Enter branch name (default: main): ").strip()
    if not branch:
        branch = 'main'

    # Get author name/pattern
    author_name = input("Enter author name (can use regex patterns): ").strip()
    if not author_name:
        print("Author name is required.")
        return

    # Get date range
    date_range = input("Enter date range (YYYY-MM-DD to YYYY-MM-DD) or press Enter to skip: ").strip()
    start_date = None
    end_date = None

    if date_range:
        try:
            dates = date_range.split("to")
            if len(dates) == 2:
                start_date = parse_date(dates[0].strip())
                end_date = parse_date(dates[1].strip())
            else:
                print("Invalid date range format. Using no date filter.")
        except Exception:
            print("Error parsing date range. Using no date filter.")

    # Get file paths
    file_paths_input = input("Enter specific file paths to analyze (comma-separated) or press Enter for all: ").strip()
    file_paths = None
    if file_paths_input:
        file_paths = [path.strip() for path in file_paths_input.split(",")]

    # Run analysis
    print("\nAnalyzing repository...")

    if is_url:
        # Clone repository first
        try:
            stats = get_author_modifications(
                repo_path=repo_input,
                author_name=author_name,
                branch=branch,
                start_date=start_date,
                end_date=end_date,
                file_paths=file_paths,
                is_cloned=True
            )
        except Exception as e:
            print(f"Error: {str(e)}")
            return
    else:
        # Local repository
        stats = get_author_modifications(
            repo_path=repo_input,
            author_name=author_name,
            branch=branch,
            start_date=start_date,
            end_date=end_date,
            file_paths=file_paths
        )

    # Display results
    display_results(stats)


def display_results(stats):
    """Format and display the results"""
    if "error" in stats:
        print(f"\nError: {stats['error']}")
        return

    print("\n=== Analysis Results ===")
    print(f"Repository: {stats['repository']}")
    print(f"Author pattern: {stats['author']}")
    print(f"Total commits: {stats['total_commits']}")

    if stats['first_commit_date']:
        print(f"First commit: {stats['first_commit_date']}")
        print(f"Last commit: {stats['last_commit_date']}")

    print("\nCode Changes:")
    print(f"  Lines added:   {stats['lines_added']:,}")
    print(f"  Lines deleted: {stats['lines_deleted']:,}")
    print(f"  Total changes: {stats['total_lines_modified']:,}")

    # Display top 10 most modified files
    if stats['file_stats']:
        print("\nTop 10 Most Modified Files:")
        sorted_files = sorted(
            stats['file_stats'].items(),
            key=lambda x: x[1]['insertions'] + x[1]['deletions'],
            reverse=True
        )

        for i, (file, changes) in enumerate(sorted_files[:10], 1):
            total = changes['insertions'] + changes['deletions']
            print(f"{i:2d}. {file} ({total:,} changes: +{changes['insertions']:,} -{changes['deletions']:,})")


def main():
    """Main function with command-line argument parsing"""
    parser = argparse.ArgumentParser(description='Analyze code modifications by author in a Git repository')

    parser.add_argument('-r', '--repo', help='Path or URL to Git repository')
    parser.add_argument('-a', '--author', help='Author name (can use regex patterns)')
    parser.add_argument('-b', '--branch', default='main', help='Branch to analyze (default: main)')
    parser.add_argument('-s', '--start-date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('-e', '--end-date', help='End date (YYYY-MM-DD)')
    parser.add_argument('-f', '--files', help='Comma-separated list of file paths to analyze')
    parser.add_argument('-t', '--token', help='Access token for private repositories')
    parser.add_argument('-u', '--username', help='Username for private repositories')
    parser.add_argument('-p', '--password', help='Password for private repositories')
    parser.add_argument('-i', '--interactive', action='store_true', help='Run in interactive mode')

    args = parser.parse_args()

    # Run in interactive mode if specified or if no arguments provided
    if args.interactive or len(sys.argv) == 1:
        interactive_mode()
        return

    # Parse arguments
    repo_path = args.repo or '.'

    if not args.author:
        print("Error: Author name is required. Use -a or --author option.")
        parser.print_help()
        return

    # Parse file paths
    file_paths = None
    if args.files:
        file_paths = [path.strip() for path in args.files.split(",")]

    # Parse dates
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)

    # Run analysis
    try:
        is_url = validate_git_url(repo_path)
        stats = get_author_modifications(
            repo_path=repo_path,
            author_name=args.author,
            branch=args.branch,
            start_date=start_date,
            end_date=end_date,
            file_paths=file_paths,
            is_cloned=is_url
        )

        # Display results
        display_results(stats)
    except Exception as e:
        print(f"Error: {str(e)}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
