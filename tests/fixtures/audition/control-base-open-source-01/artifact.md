## Conclusion

Where maintainers are paid, the differences this record describes are in how attention divides and in what surrounds the work, not in any measured increase in output. Paid maintainers report a larger share of their month on maintenance and security and a smaller share on new features than unpaid ones, while working more hours [3], and sponsorship in the project examined closely arrives wrapped in committees whose written rule keeps funders advising rather than deciding [4]. What the record does not show is money moving what a project produces; the two studies that looked found little [1][2]. The strongest opposing reading — that sponsorship selected different people rather than changing the work — is engaged below: it fits almost all of this evidence, and less well what paid maintainers say being paid changed for them [3][5].

## Key findings

- Paid maintainers report 53 percent of their time on maintenance and 13 percent on security against 48 and 10 percent for unpaid hobbyists, and 29 percent on new features against 39 percent [3].
- Asked what being paid changed, 83 percent of paid maintainers named more maintenance time, 64 percent more work on new feature requests and 52 percent better response to reported security issues [3].
- Across 345 projects aligned on the month of their first donation, the donation is not associated with any shift in commit activity [1].
- The most common answer sponsored developers gave about the effect of having sponsors was that nothing had changed; motivation was second [2].
- In the project examined in depth, member companies buy committee seats that cannot override a maintainer consensus, and one membership ended over a dispute about influence [4].
- Where paid and volunteer contributors have been compared in one project, being paid predicts long-term contribution [5].

## The strongest counterargument

The strongest case against reading any of this as a change in the work is that it is a change in who does it. Almost every comparison here sets paid people beside unpaid people rather than the same people before and after payment, and the survey reporting the time split says of another of its paid-versus-unpaid correlations that it is hard to say definitively what is cause and what is effect, asking whether projects with more maintainers simply command more income [3]. The Rust comparison runs the same way: being paid predicts long-term contribution, which its authors read as a plausible consequence of secure income but cannot separate from who gets hired [5]. The one before-and-after design found close to no effect, its authors calling their analysis some evidence but not strong support for donations raising activity [1]. What the selection reading fits less well is the part of the record holding the person fixed: paid maintainers asked what being paid changed for them named more maintenance time, better security response and more feature work [3]. That is self-report about one's own past, and weak, but it is the only evidence here from inside the change.

## Scale and spread

How far this reaches, and how quickly it arrived, is not something this record settles, and this report does not claim it. The maintainer survey drew 437 respondents through a social-media campaign rather than a sample of projects [3]; the sponsorship study says its own subjects are early adopters whose results do not generalize to open-source developers at large [2]; the funding study is one project [4] and the paid-versus-volunteer comparison one project [5]. None reports what share of widely used projects is sponsored, and none dates a transition. What follows is therefore a claim about kind and not about prevalence — these are the differences the record describes where sponsorship is present, and how large that population is remains open.

## What the money is reported to change

The clearest reported change is in how the work divides. Asked to divide a month across categories, the paid group put 53 percent of its time into day-to-day maintenance — documentation, review of contributions, dependency management, triage — and 29 percent into new features, against 48 and 39 percent for unpaid hobbyists; security moved the same way and by less, 13 percent against 10 [3]. Shares are not amounts: in the same survey 82 percent of professional maintainers spend more than twenty hours a week on their projects, against 78 percent of unpaid hobbyists spending ten hours or fewer [3]. Each gap is a few points wide, on a self-selected sample of 437, and the survey presents the split as a tradeoff every maintainer makes with limited time [3].

What did not appear is a measured increase in output. Aligning 345 projects on the month they first received a donation, the analysis of commits and issue-resolution speed finds no shift attributable to the donation, though higher total funding does associate with more activity, explaining little of the variance [1]. The sponsorship survey found that absence from both sides: more sponsored developers said having sponsors had changed nothing than said anything else, and most sponsors reported observing no effect [2].

## What it changes around the work

Money arrives attached to a structure. In the project examined in depth, corporate funding runs through a consortium whose tiered annual memberships buy places on a technical and an advisory committee, and the maintainers wrote the rule that the technical committee cannot override a decision they reached by consensus [4]. The same account records a sponsor asking for documentation changes the maintainers read as advertising and refusing, and a membership ending over such a dispute [4]. What sponsors there say they buy is narrower: maintenance of a dependency, and goodwill useful in hiring [4].

The other side is a relationship the money does not settle. In that survey the reasons sponsors gave most often were depending on the recipient's code and recognizing the work, and most reported no specific expectation and no observed result [2]. The case study is blunter about who is absent: the companies filing the most issues and pull requests against the project are largely not among its funders, and its community manager describes those contributions as adding to the maintainers' work rather than relieving it [4].

## Sources

1. Overney, C., Meinicke, J., Kästner, C., & Vasilescu, B. (2020). How to Not Get Rich: An Empirical Study of Donations in Open Source. *ICSE 2020*. doi:10.1145/3377811.3380410.
2. Shimada, N., Xiao, T., Hata, H., Treude, C., & Matsumoto, K. (2022). GitHub Sponsors: Exploring a New Way to Contribute to Open Source. *ICSE 2022*. doi:10.1145/3510003.3510116.
3. Tidelift (2024). *The 2024 Tidelift State of the Open Source Maintainer Report*, September 2024.
4. Osborne, C. (2024). *Public-Private Funding Models in Open Source Software Development: A Case Study on scikit-learn*. arXiv:2404.06484v5.
5. Zhang, Y., Qin, M., Stol, K.-J., Zhou, M., & Liu, H. (2024). How Are Paid and Volunteer Open Source Developers Different? A Study of the Rust Project. *ICSE 2024*. doi:10.1145/3597503.3639197.
