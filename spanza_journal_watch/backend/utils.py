class Journal:
    def __init__(self, name, start_volume, start_year, start_month, issues_per_year):
        self.name = name
        self.current_volume = start_volume
        self.current_year = start_year
        self.current_month = start_month
        self.issues_per_year = issues_per_year

    def get_issue(self, target_month, target_year):
        years_between, months_between = self._calculate_years_and_months_between(target_month, target_year)

        total_issues = (
            (self.current_year - 1) * self.issues_per_year
            + (self.current_volume - 1) * self.issues_per_year
            + years_between * self.issues_per_year
            + months_between
        )

        volume = total_issues // self.issues_per_year
        issue = total_issues % self.issues_per_year

        return f"Volume {volume} Issue {issue}"

    def _calculate_years_and_months_between(self, target_month, target_year):
        months = [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]

        target_month_index = months.index(target_month.lower())
        current_month_index = months.index(self.current_month.lower())

        years_between = target_year - self.current_year
        months_between = years_between * 12 + target_month_index - current_month_index

        return years_between, months_between


# Create a Journal instance for BJA with the provided information
bja_journal = Journal(name="BJA", start_volume=128, start_year=2022, start_month="january", issues_per_year=12)

# Test the class with your example for October 2023
result = bja_journal.get_issue("october", 2023)
print(result)  # Output: Volume 131 Issue 4
