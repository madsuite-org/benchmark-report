# Write the benchmark report, a static site, from result directories.
#
#     julia --project bin/build.jl ROOT SITE [--base=REV|DIR] [--wall=7200] [--title=TEXT] [--repo=CLONE] [--link=URL]
#
# ROOT holds one directory of result files per machine, as run.jl writes them. SITE is the
# directory of the report. `--base` adds the results of a directory or an archived revision.

using BenchmarkReport

function main(args)
    opt = Dict{String, String}("wall" => "7200", "title" => "MadNLP benchmark")
    pos = String[]
    for a in args
        m = match(r"^--(base|wall|title|link|repo)=(.*)$", a)
        m === nothing ? push!(pos, a) : (opt[m[1]] = m[2])
    end
    length(pos) == 2 || error("usage: build.jl ROOT SITE [--base=REV|DIR] [--wall=7200] [--title=TEXT] [--link=URL] [--repo=CLONE]")
    haskey(opt, "repo") && (BenchmarkReport.REPOSITORY[] = opt["repo"])
    base = haskey(opt, "base") ? locate(opt["base"], nothing) : nothing
    reference = haskey(opt, "repo") ? ipopt_reference(benchmark_version(pos[1])) : nothing
    write_report(pos[2], pos[1]; base, wall = parse(Float64, opt["wall"]), title = opt["title"], link = get(opt, "link", nothing), reference, finished = true)
    return
end

main(ARGS)
