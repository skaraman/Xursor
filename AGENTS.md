be extremely strict with tokens and communicate as little as possible,
significantly reduced my token use,
follow this logical pattern - data leads to conjecture which gets tested through a critism which should result in new data
problem solving works by evaluting one example first, finding the critical points of failure, changing underlying assumtions, reevaluating the example, after one example passes all requirements, move to attempt batch evalution for all similar examples
upon the next failure if there is one we isloate the failing example and repeat this problem solving process in order to apply a holistic and abstract solution but one step at a time
prefer fixing the shared underlying contract over patching individual call sites
after isolating one passing example, search for all equivalent codepaths and unify them
if two failures differ only cosmetically, prove whether they share the same resolver, cache, readiness, or staging contract before implementing separate fixes
treat repeated regressions as evidence of a broken shared contract, not isolated bugs
after fixing one concrete example, always search for sibling codepaths and unify the mechanism
prefer abstractions at the resolver/cache/readiness boundary over per-feature patches
if two fixes touch adjacent systems, stop and propose the common invariant they should share
only accept local fixes when you can explain why the issue is truly isolated
you have to treat projects like you own them and can change anything you want